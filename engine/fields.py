"""The extraction schema a benchmark scores against.

ProofBench began as an invoice-extraction benchmark, so the four invoice fields
were a module constant and normalization dispatched on the field's *name*:
``date`` parsed as a date, ``total`` as an amount, everything else as text. That
made every other extraction task unscoreable — a receipts, purchase-order, or
contract benchmark has different columns, and nothing downstream could grade it.

A field therefore carries a declared *type*, and normalization dispatches on the
type. A benchmark can name whatever columns its documents actually have, and the
comparison stays exact: dates still compare as dates whether the column is called
``date``, ``issued_on``, or ``due``.

Types are deliberately few. Each one exists because it changes how two values are
judged equal, not because it describes the data prettily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# text     compare as case-folded, whitespace-collapsed text
# date     compare as a calendar date, so 03/04/2026 and 2026-04-03 agree
# currency compare as a monetary amount in minor units, ignoring symbol and
#          thousands separators, so "$1,234.50" and "1234.5" agree
# number   compare as a plain number, without currency's minor-unit scaling
FIELD_TYPES = ("text", "date", "currency", "number")

MAX_FIELDS = 32
MAX_FIELD_NAME_CHARS = 64


@dataclass(frozen=True)
class Field:
    """One column of the extraction schema."""

    name: str
    type: str = "text"

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > MAX_FIELD_NAME_CHARS:
            raise ValueError("invalid field name")
        if self.type not in FIELD_TYPES:
            raise ValueError(f"unknown field type: {self.type}")


# The original invoice schema, still the default so existing specs, datasets, and
# stored runs keep their exact previous meaning.
DEFAULT_FIELDS: tuple[Field, ...] = (
    Field("invoice_number", "text"),
    Field("date", "date"),
    Field("vendor", "text"),
    Field("total", "currency"),
)

# Names that carried a type implicitly before types existed. Applied only when a
# caller supplies bare field names, so legacy specs normalize as they always did.
_LEGACY_TYPES = {"date": "date", "total": "currency"}


def infer_type(name: str) -> str:
    return _LEGACY_TYPES.get(name, "text")


def parse_fields(value: Any) -> tuple[Field, ...]:
    """Build a schema from bare names, dicts, or existing Fields.

    Bare names keep their historical typing, so ``["invoice_number", "date",
    "vendor", "total"]`` still means exactly what it used to mean.
    """
    if value is None:
        return DEFAULT_FIELDS
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("fields must be a list")
    parsed: list[Field] = []
    for item in value:
        if isinstance(item, Field):
            parsed.append(item)
        elif isinstance(item, str):
            parsed.append(Field(item, infer_type(item)))
        elif isinstance(item, dict):
            name = str(item.get("name") or "")
            declared = item.get("type")
            parsed.append(Field(name, str(declared) if declared else infer_type(name)))
        else:
            raise ValueError("each field must be a name or a {name, type} object")
    if not parsed:
        raise ValueError("a benchmark needs at least one field")
    if len(parsed) > MAX_FIELDS:
        raise ValueError("too many fields")
    names = [field.name for field in parsed]
    if len(set(names)) != len(names):
        raise ValueError("field names must be unique")
    return tuple(parsed)


def field_names(fields: Iterable[Field]) -> tuple[str, ...]:
    return tuple(field.name for field in fields)


def normalize_value(field: Field, value: Any) -> str:
    """Reduce one value to the canonical form its type compares in."""
    # Imported here: evaluate imports this module for the schema, and the
    # normalizers live there with the rest of the scoring primitives.
    from engine.evaluate import normalize_amount, normalize_date, normalize_text

    if field.type == "date":
        return normalize_date(value)
    if field.type == "currency":
        amount = normalize_amount(value)
        return "" if amount is None else str(amount)
    if field.type == "number":
        text = normalize_text(value).replace(",", "")
        try:
            number = float(text)
        except (TypeError, ValueError):
            return ""
        return str(int(number)) if number.is_integer() else str(number)
    return normalize_text(value)
