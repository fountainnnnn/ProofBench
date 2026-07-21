"""Strict API payload schemas."""
from __future__ import annotations

import re
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_RE = re.compile(r"^[a-f0-9]{12}$")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
EXTRACTION_FIELDS = ["invoice_number", "date", "vendor", "total"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ProofBench is real-only. `mode` survives solely so an older client that still
# sends it keeps working; the sole accepted value is "real", so an explicit
# "demo" fails schema validation (422) before any session or run is mutated.
RunMode = Literal["real"]


class ChatRequest(StrictModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=10_000)
    dataset_id: str | None = None
    mode: RunMode = "real"

    @field_validator("session_id", "dataset_id")
    @classmethod
    def valid_ids(cls, value: str | None) -> str | None:
        if value is not None and not ID_RE.fullmatch(value):
            raise ValueError("must be a 12-character lowercase hexadecimal id")
        return value


class CandidateSpec(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    docs_url: str = Field(default="", max_length=2048)
    pricing_url: str = Field(default="", max_length=2048)
    kind: Literal["local_tool", "hosted_api"]
    # Opt in to ProofBench's own first-party adapter for this candidate. It only
    # has an effect for names in engine.builtin_adapters; the server, not this
    # flag, decides whether a candidate is trusted and which exact credentials
    # it receives. A generated candidate that borrows a built-in name gets none.
    use_fallback: bool = True

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not NAME_RE.fullmatch(value):
            raise ValueError("invalid candidate name")
        return value

    @field_validator("docs_url", "pricing_url")
    @classmethod
    def valid_public_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("must be a public HTTP(S) URL without credentials")
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ValueError("local URLs are not accepted")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("private and reserved URLs are not accepted")
        return value


class DatasetSpec(StrictModel):
    dataset_id: str | None = None
    path: str | None = Field(default=None, max_length=4096)

    @field_validator("dataset_id")
    @classmethod
    def valid_dataset_id(cls, value: str | None) -> str | None:
        if value is not None and not ID_RE.fullmatch(value):
            raise ValueError("invalid dataset id")
        return value


class BenchmarkSpec(StrictModel):
    benchmark_type: Literal["extraction"] = "extraction"
    category: str = Field(min_length=1, max_length=128)
    fields: list[str] = Field(min_length=1, max_length=32)
    candidates: list[CandidateSpec] = Field(min_length=1, max_length=20)
    dataset: DatasetSpec | None = None

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, fields: list[str]) -> list[str]:
        if any(not NAME_RE.fullmatch(field) for field in fields):
            raise ValueError("invalid field name")
        if fields != EXTRACTION_FIELDS:
            raise ValueError("fields must be invoice_number, date, vendor, total in that order")
        return fields

    @model_validator(mode="after")
    def unique_candidates(self):
        names = [candidate.name for candidate in self.candidates]
        if len(set(names)) != len(names):
            raise ValueError("candidate names must be unique")
        return self


class AssessmentCandidateSpec(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    docs_url: str = Field(min_length=1, max_length=2048)
    pricing_url: str = Field(default="", max_length=2048)
    kind: Literal["local_tool", "hosted_api", "saas"]

    _valid_name = field_validator("name")(CandidateSpec.valid_name.__func__)
    _valid_urls = field_validator("docs_url", "pricing_url")(CandidateSpec.valid_public_url.__func__)


class ToolAssessmentSpec(StrictModel):
    benchmark_type: Literal["tool_assessment"]
    category: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=4000)
    candidates: list[AssessmentCandidateSpec] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_candidates(self):
        names = [candidate.name for candidate in self.candidates]
        if len(set(names)) != len(names):
            raise ValueError("candidate names must be unique")
        return self


class RunRequest(StrictModel):
    spec: Annotated[BenchmarkSpec | ToolAssessmentSpec, Field(discriminator="benchmark_type")]
    mode: RunMode = "real"

    @model_validator(mode="before")
    @classmethod
    def default_extraction_discriminator(cls, value):
        if isinstance(value, dict) and isinstance(value.get("spec"), dict):
            value = dict(value)
            spec = dict(value["spec"])
            spec.setdefault("benchmark_type", "extraction")
            value["spec"] = spec
        return value


class SyntheticDatasetRequest(StrictModel):
    use_synthetic: Literal[True]


class AuthSessionRequest(StrictModel):
    token: str = Field(min_length=1, max_length=16_384)


class ProviderKeyRequest(StrictModel):
    env: str = Field(min_length=2, max_length=128)
    value: str = Field(min_length=1, max_length=16_384)

    @model_validator(mode="after")
    def key_length(self):
        if self.env.upper().endswith("_API_KEY") and len(self.value) < 8:
            raise ValueError("provider API keys must contain at least 8 characters")
        return self
