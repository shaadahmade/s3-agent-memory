"""Gate C — robustness & failure honesty."""

from __future__ import annotations

import pytest

from s3_agent_memory import AnnotationNotFound, SummaryMemory, parse_s3_uri

from .conftest import TEST_BUCKET, TEST_KEY


def test_c1_recall_empty_object(alpha):
    assert alpha.recall(include_all_agents=True) == []


def test_c2_malformed_annotation_flagged(alpha, fake):
    """A malformed JSON payload under a mem.* name is flagged, others survive."""
    alpha.remember(SummaryMemory(agent_id="alpha", text="valid one"))
    fake.plant_raw_annotation(
        TEST_BUCKET, TEST_KEY, "mem.alpha.summary.broken", b"{not json at all"
    )

    got = alpha.recall(kind="summary")
    by_valid = {m.invalid for m in got}
    assert by_valid == {True, False}  # both a valid and an invalid one returned
    bad = [m for m in got if m.invalid][0]
    assert bad.record is None
    assert bad.error is not None
    good = [m for m in got if not m.invalid][0]
    assert good.record.text == "valid one"


def test_c3_race_delete_between_list_and_get(alpha, fake):
    """An annotation vanishing between list and get is skipped, not raised."""
    alpha.remember(SummaryMemory(agent_id="alpha", text="stable"), slot="keep")
    alpha.remember(SummaryMemory(agent_id="alpha", text="doomed"), slot="race")
    fake.schedule_vanish_on_get(TEST_BUCKET, TEST_KEY, "mem.alpha.summary.race")

    got = alpha.recall(kind="summary")
    slots = {m.slot for m in got}
    assert slots == {"keep"}  # the raced one was silently skipped


def test_c4_forget_nonexistent_raises(alpha):
    with pytest.raises(AnnotationNotFound):
        alpha.forget("summary", slot="never-written")


def test_c5_malformed_uri_raises_with_offender():
    for bad in ["", "http://x/y", "s3://", "s3:///key-no-bucket", "s3://bucket-only"]:
        with pytest.raises(ValueError) as exc:
            parse_s3_uri(bad)
        assert repr(bad) in str(exc.value)
