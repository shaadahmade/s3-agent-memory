"""Gate A — round-trip integrity."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from s3_agent_memory import (
    MAX_ANNOTATION_BYTES,
    FindingMemory,
    MemoryTooLarge,
    SummaryMemory,
    to_payload,
)


def test_a1_roundtrip_deep_equality(alpha):
    """remember() then recall() yields the exact record, tz-aware datetime kept."""
    rec = SummaryMemory(
        agent_id="alpha",
        created_at=datetime(2026, 7, 22, 14, 30, 15, 123456, tzinfo=timezone.utc),
        text="The Q3 report undercounts EMEA revenue by ~4%.",
        related_uris=["s3://research-bucket/reports/2026/q2.parquet"],
    )
    alpha.remember(rec)

    got = alpha.recall(kind="summary")
    assert len(got) == 1
    assert got[0].invalid is False
    assert got[0].record == rec
    # datetime survived tz-aware and to the microsecond.
    assert got[0].record.created_at == rec.created_at
    assert got[0].record.created_at.tzinfo is not None


def test_a2_unicode_byte_exact(alpha):
    """Unicode payloads (Hindi, CJK, symbols) survive byte-exact."""
    text = "उत्पादन बढ़ा — 利益率 ↑↑ ∑Δ 見積もり"
    rec = SummaryMemory(agent_id="alpha", text=text)
    alpha.remember(rec)

    got = alpha.recall(kind="summary")
    assert got[0].record.text == text
    # the stored payload decodes back to the identical string
    assert got[0].raw.decode("utf-8")  # no decode error
    assert text in got[0].raw.decode("utf-8")


def test_a3_size_limit_boundary(alpha):
    """Exactly 1,000,000 bytes accepted; 1,000,001 raises MemoryTooLarge."""
    base = SummaryMemory(
        agent_id="alpha",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        text="",
    )
    overhead = len(to_payload(base))
    # ASCII 'a' is one JSON byte each, so we can hit an exact total.
    exact_text = "a" * (MAX_ANNOTATION_BYTES - overhead)
    ok = base.model_copy(update={"text": exact_text})
    assert len(to_payload(ok)) == MAX_ANNOTATION_BYTES
    alpha.remember(ok)  # accepted

    too_big = base.model_copy(update={"text": exact_text + "a"})
    assert len(to_payload(too_big)) == MAX_ANNOTATION_BYTES + 1
    with pytest.raises(MemoryTooLarge) as exc:
        alpha.remember(too_big)
    msg = str(exc.value)
    assert "1 MB" in msg
    assert "slot" in msg.lower() or "pointer" in msg.lower()


def test_a4_annotation_count_limit(alpha):
    """The 1,001st distinct annotation name on one object raises a clear error."""
    from s3_agent_memory import AnnotationLimitExceeded

    # 1,000 distinct slots fill the object exactly to the S3 limit.
    for i in range(1000):
        alpha.remember(SummaryMemory(agent_id="alpha", text=f"m{i}"), slot=f"s{i}")

    with pytest.raises(AnnotationLimitExceeded):
        alpha.remember(SummaryMemory(agent_id="alpha", text="one too many"), slot="s1000")
