"""S3Memory — durable, object-attached agent memory (the fresh read path).

An :class:`S3Memory` binds one agent identity to one S3 object. It may WRITE only
under its own ``agent_id`` namespace but may READ every agent's memories on the
object. Reads here are direct ``GetObjectAnnotation`` calls and are therefore
always fresh — contrast with :class:`s3_agent_memory.query.AthenaMemoryQuery`,
which is fleet-wide but lags ~1 hour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from .errors import AnnotationNotFound, MemoryTooLarge
from .raw import RawAnnotationClient
from .schemas import (
    MAX_ANNOTATION_BYTES,
    MemoryRecord,
    from_payload,
    to_payload,
)

# Segment charset for agent_id / slot: no dots (the namespace delimiter), no
# whitespace. Keeps `mem.{agent}.{kind}.{slot}` unambiguously parseable.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_NAMESPACE_PREFIX = "mem"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key`` into ``(bucket, key)``.

    Raises ``ValueError`` naming the offending string on anything malformed
    (contract/eval C5).
    """
    if not isinstance(uri, str) or not uri:
        raise ValueError(f"invalid s3 URI (empty or not a string): {uri!r}")
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"invalid s3 URI (scheme must be 's3://'): {uri!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket:
        raise ValueError(f"invalid s3 URI (missing bucket): {uri!r}")
    if not key:
        raise ValueError(f"invalid s3 URI (missing object key): {uri!r}")
    return bucket, key


def _validate_segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SEGMENT_RE.match(value or ""):
        raise ValueError(
            f"invalid {label} {value!r}: must match [A-Za-z0-9_-]+ "
            "(no dots, no whitespace)"
        )
    return value


@dataclass
class RecalledMemory:
    """One annotation read back off an object.

    ``record`` is the validated model, or ``None`` when the payload failed
    validation — in which case ``invalid`` is ``True`` and ``error`` explains
    why. Callers get every memory, valid or not; nothing is silently dropped.
    """

    name: str
    agent_id: str
    kind: str
    slot: Optional[str]
    record: Optional[MemoryRecord]
    raw: bytes
    invalid: bool = False
    error: Optional[str] = None


class S3Memory:
    """Durable memory bound to ``(bucket, key)`` for a single ``agent_id``."""

    def __init__(
        self,
        uri: str,
        agent_id: str,
        raw: RawAnnotationClient,
    ):
        self.bucket, self.key = parse_s3_uri(uri)
        self.uri = uri
        self.agent_id = _validate_segment(agent_id, "agent_id")
        if not isinstance(raw, RawAnnotationClient):
            raise TypeError("raw must be a RawAnnotationClient instance")
        self._raw = raw

    # -- name helpers ----------------------------------------------------

    def _name_for(self, kind: str, slot: Optional[str], agent_id: str) -> str:
        parts = [_NAMESPACE_PREFIX, agent_id, kind]
        if slot is not None:
            parts.append(_validate_segment(slot, "slot"))
        return ".".join(parts)

    @staticmethod
    def _parse_name(name: str) -> Optional[tuple[str, str, Optional[str]]]:
        """Return ``(agent_id, kind, slot)`` for a library annotation, else None."""
        parts = name.split(".")
        if len(parts) not in (3, 4) or parts[0] != _NAMESPACE_PREFIX:
            return None
        agent_id, kind = parts[1], parts[2]
        slot = parts[3] if len(parts) == 4 else None
        return agent_id, kind, slot

    # -- write -----------------------------------------------------------

    def remember(self, record: MemoryRecord, slot: Optional[str] = None) -> str:
        """Write a memory under THIS agent's namespace and return its name.

        The caller's ``record.agent_id`` is always overwritten with this
        instance's ``agent_id`` — an agent cannot write into another agent's
        namespace even by forging the field (contract item 1, eval B1).
        Writing the same ``(kind, slot)`` twice is last-writer-wins (eval B3).
        """
        if not isinstance(record, MemoryRecord):
            raise TypeError(f"record must be a MemoryRecord, got {type(record).__name__}")

        # Overwrite forged identity, then re-validate the corrected model.
        record = record.model_copy(update={"agent_id": self.agent_id})
        record = type(record).model_validate(record.model_dump())

        payload = to_payload(record)
        if len(payload) > MAX_ANNOTATION_BYTES:
            raise MemoryTooLarge(
                f"serialized memory is {len(payload):,} bytes, over the 1 MB "
                f"({MAX_ANNOTATION_BYTES:,} bytes) per-annotation S3 limit. "
                "Remediation: split the payload across slots (slot=...), or store "
                "the bulk in a separate S3 object and keep only a pointer URI here."
            )

        name = self._name_for(record.kind, slot, self.agent_id)
        self._raw.put_annotation(self.bucket, self.key, name, payload)
        return name

    # -- read ------------------------------------------------------------

    def recall(
        self,
        kind: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
        include_all_agents: bool = False,
    ) -> list[RecalledMemory]:
        """Read memories off this object, always fresh (direct GET).

        By default returns only THIS agent's memories. Pass
        ``include_all_agents=True`` for every agent, or ``agent_id="beta"`` to
        isolate one. Non-library annotations (no ``mem.`` prefix) are ignored;
        invalid payloads are returned flagged, never dropped (contract item 2).
        """
        if agent_id is not None:
            target_agent = agent_id
        elif include_all_agents:
            target_agent = None
        else:
            target_agent = self.agent_id

        out: list[RecalledMemory] = []
        for name in self._raw.list_annotation_names(self.bucket, self.key):
            parsed = self._parse_name(name)
            if parsed is None:
                continue  # foreign annotation (e.g. "mediainfo") — not ours
            a_id, a_kind, a_slot = parsed
            if target_agent is not None and a_id != target_agent:
                continue
            if kind is not None and a_kind != kind:
                continue

            try:
                raw = self._raw.get_annotation(self.bucket, self.key, name)
            except AnnotationNotFound:
                # Raced: deleted between list and get. Skip it, don't crash.
                continue

            record, error = from_payload(raw)
            out.append(
                RecalledMemory(
                    name=name,
                    agent_id=a_id,
                    kind=a_kind,
                    slot=a_slot,
                    record=record,
                    raw=raw,
                    invalid=record is None,
                    error=error,
                )
            )
        return out

    def recall_text(
        self,
        max_chars: int = 4000,
        *,
        kind: Optional[str] = None,
        agent_id: Optional[str] = None,
        include_all_agents: bool = True,
    ) -> str:
        """Render recalled memories as a compact text block for an LLM prompt.

        Guarantees: output length ≤ ``max_chars``; every included memory line
        names its ``agent_id`` and ``kind``; invalid payloads are shown flagged
        (eval D3). Defaults to all agents — a successor wants the whole object's
        knowledge, not just its own.
        """
        memories = self.recall(
            kind=kind, agent_id=agent_id, include_all_agents=include_all_agents
        )
        if not memories:
            return ""

        lines: list[str] = []
        for m in memories:
            tag = f"{m.agent_id}/{m.kind}" + (f".{m.slot}" if m.slot else "")
            if m.invalid:
                body = f"[INVALID: {m.error}]"
            else:
                body = _summarize_record(m.record)
            lines.append(f"- [{tag}] {body}")

        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text

        # Truncate whole lines so every surviving memory keeps its agent_id/kind.
        kept: list[str] = []
        used = 0
        for line in lines:
            add = len(line) + (1 if kept else 0)
            if used + add > max_chars:
                break
            kept.append(line)
            used += add
        return "\n".join(kept)

    # -- delete ----------------------------------------------------------

    def forget(
        self,
        kind: str,
        slot: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
    ) -> None:
        """Delete one memory. Raises ``AnnotationNotFound`` if it doesn't exist.

        S3's DeleteObjectAnnotation is idempotent server-side, so we verify
        existence first to make ``forget`` honest about missing memories
        (eval C4). ``agent_id`` defaults to this agent (you may forget your own).
        """
        owner = agent_id if agent_id is not None else self.agent_id
        name = self._name_for(kind, slot, owner)
        existing = self._raw.list_annotation_names(self.bucket, self.key)
        if name not in existing:
            raise AnnotationNotFound(
                f"no memory {name!r} on s3://{self.bucket}/{self.key}"
            )
        self._raw.delete_annotation(self.bucket, self.key, name)


def _summarize_record(record: MemoryRecord) -> str:
    """Best-effort one-line human summary of a memory for recall_text."""
    for field in ("text", "value", "key"):
        val = getattr(record, field, None)
        if isinstance(val, str) and val:
            return val
    return record.model_dump_json()
