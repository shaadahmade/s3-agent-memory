"""Pydantic v2 memory schemas and the (de)serialization envelope.

Every memory written by the library is a validated Pydantic model. On the wire a
memory is stored as the UTF-8 JSON produced by ``model_dump_json()``. The ``kind``
field is a discriminator used on read to pick the concrete class back out of
:data:`MEMORY_TYPES`.

Contract item 2: every write passes a schema; every read re-validates and
invalid records are surfaced (never silently dropped). The read-side helper
:func:`from_payload` therefore never raises on bad data — it returns a
``(record, error)`` pair so the caller can flag ``invalid`` and keep going.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# The maximum size, in bytes, of a single annotation payload (S3: 1 MB).
MAX_ANNOTATION_BYTES = 1_000_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryRecord(BaseModel):
    """Base class for all durable memories.

    Subclasses fix ``kind`` to a string literal; that literal is both the
    on-disk discriminator and the ``kind`` segment of the annotation name
    (``mem.{agent_id}.{kind}[.{slot}]``).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    agent_id: str = Field(..., min_length=1)
    created_at: datetime = Field(default_factory=_utcnow)
    # Overridden by every subclass with a Literal default.
    kind: str


class SummaryMemory(MemoryRecord):
    """A distilled summary an agent wants a successor to inherit."""

    kind: Literal["summary"] = "summary"
    text: str
    related_uris: list[str] = Field(default_factory=list)


class FindingMemory(MemoryRecord):
    """A concrete finding/observation, with a confidence and provenance links."""

    kind: Literal["finding"] = "finding"
    text: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    related_uris: list[str] = Field(default_factory=list)


class FactMemory(MemoryRecord):
    """A single key/value fact learned about the attached object or task."""

    kind: Literal["fact"] = "fact"
    key: str
    value: str


# Discriminator registry: kind -> concrete class. Extend this to add memory
# types; the client and query layers derive namespacing from the keys.
MEMORY_TYPES: dict[str, type[MemoryRecord]] = {
    cls.model_fields["kind"].default: cls
    for cls in (SummaryMemory, FindingMemory, FactMemory)
}


def to_payload(record: MemoryRecord) -> bytes:
    """Serialize a memory to the exact bytes stored in the S3 annotation."""
    return record.model_dump_json().encode("utf-8")


def from_payload(raw: bytes) -> tuple[Optional[MemoryRecord], Optional[str]]:
    """Deserialize + re-validate a payload.

    Returns ``(record, None)`` on success or ``(None, reason)`` when the bytes
    are not a valid, known memory. Never raises — invalid data is data, and the
    caller surfaces it with ``invalid=True`` rather than crashing the recall.
    """
    import json

    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"not valid UTF-8 JSON: {exc}"

    if not isinstance(obj, dict):
        return None, f"top-level JSON is {type(obj).__name__}, expected object"

    kind = obj.get("kind")
    cls = MEMORY_TYPES.get(kind)
    if cls is None:
        return None, f"unknown memory kind: {kind!r}"

    try:
        return cls.model_validate(obj), None
    except ValidationError as exc:
        return None, f"schema validation failed: {exc.error_count()} error(s)"
