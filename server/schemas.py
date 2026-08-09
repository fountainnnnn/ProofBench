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
    # source="generate" is intake saying this measured benchmark needs labelled
    # examples that do not exist yet. The server builds them from the spec's own
    # field schema at run start, so the run is never blocked on the user having
    # attached data first. A dataset_id, once bound, always wins over it.
    dataset_id: str | None = None
    path: str | None = Field(default=None, max_length=4096)
    source: Literal["generate"] | None = None

    @field_validator("dataset_id")
    @classmethod
    def valid_dataset_id(cls, value: str | None) -> str | None:
        if value is not None and not ID_RE.fullmatch(value):
            raise ValueError("invalid dataset id")
        return value


class FieldSpec(StrictModel):
    """One column of an extraction schema.

    The type decides how two values are judged equal by the deterministic
    evaluator (engine/fields.py): dates compare as calendar dates, currency as
    minor units, and so on. A bare string in `fields` is accepted for backwards
    compatibility and carries the typing those names always had.
    """

    name: str = Field(min_length=1, max_length=64)
    type: Literal["text", "date", "currency", "number"] = "text"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not NAME_RE.fullmatch(value):
            raise ValueError("invalid field name")
        return value


class BenchmarkSpec(StrictModel):
    benchmark_type: Literal["extraction"] = "extraction"
    category: str = Field(min_length=1, max_length=128)
    fields: list[str | FieldSpec] = Field(min_length=1, max_length=32)
    candidates: list[CandidateSpec] = Field(min_length=1, max_length=20)
    dataset: DatasetSpec | None = None

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, fields: list) -> list:
        names = []
        for field in fields:
            if isinstance(field, str):
                if not NAME_RE.fullmatch(field):
                    raise ValueError("invalid field name")
                names.append(field)
            else:
                names.append(field.name)
        if len(set(names)) != len(names):
            raise ValueError("field names must be unique")
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
    # Whether this is something to buy or a piece of a self-built integration.
    # Intake stamps it on every candidate, and StrictModel forbids extras, so
    # omitting it here rejected every normalized spec at /run with a 422 — the
    # build path is claimed explicitly, never inferred, so the default is
    # "product" for a caller that does not say.
    role: Literal["product", "build_component"] = "product"

    _valid_name = field_validator("name")(CandidateSpec.valid_name.__func__)
    _valid_urls = field_validator("docs_url", "pricing_url")(CandidateSpec.valid_public_url.__func__)


class SpecConstraints(StrictModel):
    """What the user said their environment is, as intake recorded it.

    Every field is optional: an absent constraint means the user never stated
    one, which must never be filled in on their behalf.
    """

    stack: list[str] = Field(default_factory=list, max_length=12)
    must_have: list[str] = Field(default_factory=list, max_length=12)
    budget: str = Field(default="", max_length=300)
    deployment: str = Field(default="", max_length=300)

    @field_validator("stack", "must_have")
    @classmethod
    def valid_constraint_items(cls, values: list[str]) -> list[str]:
        for item in values:
            if not item.strip():
                raise ValueError("constraint entries must not be empty")
            if len(item) > 120:
                raise ValueError("constraint entries must be at most 120 characters")
        return values


class ExcludedCandidateSpec(StrictModel):
    """A candidate that left the field before assessment, and why.

    It carries no score of any kind: it was removed before assessment, so
    nothing about it was ever measured. ``kind`` separates the two ways that
    happens — "violation" is a stated constraint ruling a candidate out, while
    "not_assessed" is one discovery surfaced and intake did not shortlist, which
    is a choice about attention rather than a strike against the tool.
    """

    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=160)
    kind: Literal["violation", "not_assessed"] = "violation"
    violates: str = Field(min_length=1, max_length=300)

    _valid_name = field_validator("name")(CandidateSpec.valid_name.__func__)


class ToolAssessmentSpec(StrictModel):
    benchmark_type: Literal["tool_assessment"]
    category: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=4000)
    constraints: SpecConstraints | None = None
    candidates: list[AssessmentCandidateSpec] = Field(min_length=1, max_length=20)
    excluded: list[ExcludedCandidateSpec] = Field(default_factory=list, max_length=20)

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


class GenerateDatasetRequest(StrictModel):
    """Ask the AI to design and render a labelled dataset for this benchmark.

    The model proposes the schema and ground-truth rows; a deterministic
    renderer draws the images. The response carries a preview so the user can
    approve or regenerate before anything runs against it.
    """

    prompt: str = Field(min_length=3, max_length=2_000)
    n: int = Field(default=12, ge=5, le=30)


class AuthSessionRequest(StrictModel):
    token: str = Field(min_length=1, max_length=16_384)


class ScraperOrderRequest(StrictModel):
    """Which scraping provider is tried first. Bounded, but not validated against
    the known names here: engine.scrapers normalizes the list so an unknown or
    stale name is dropped rather than rejected, which keeps a bad stored value
    from being able to stop a deployment scraping at all."""
    order: list[str] = Field(min_length=1, max_length=8)


class ProviderKeyRequest(StrictModel):
    env: str = Field(min_length=2, max_length=128)
    value: str = Field(min_length=1, max_length=16_384)

    @model_validator(mode="after")
    def key_length(self):
        if self.env.upper().endswith("_API_KEY") and len(self.value) < 8:
            raise ValueError("provider API keys must contain at least 8 characters")
        return self


class ProviderKeyRevealRequest(StrictModel):
    env: str = Field(min_length=2, max_length=128)


class SettingOptionsRequest(StrictModel):
    """Which non-secret setting the agent should research values for."""

    env: str = Field(min_length=2, max_length=128)


class DefaultsRequest(StrictModel):
    """An omitted field leaves that default alone; an empty string clears it."""

    orchestration: str | None = Field(default=None, max_length=64)
    assessment: str | None = Field(default=None, max_length=64)
    codegen: str | None = Field(default=None, max_length=64)
    scraper_order: list[str] | None = Field(default=None, min_length=1, max_length=8)


class IntegrationAgentHistoryItem(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class IntegrationAgentMessageRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4_000)
    history: list[IntegrationAgentHistoryItem] = Field(default_factory=list, max_length=12)
