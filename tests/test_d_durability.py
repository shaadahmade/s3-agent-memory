"""Gate D — durability semantics (the library's reason to exist)."""

from __future__ import annotations

from s3_agent_memory import (
    FindingMemory,
    RawAnnotationClient,
    S3Memory,
    SummaryMemory,
)

from .conftest import TEST_BUCKET, TEST_KEY, TEST_URI


def test_d1_annotations_travel_on_copy(alpha, beta, fake):
    """Copying the object carries every memory to the copy. This is the thesis."""
    alpha.remember(SummaryMemory(agent_id="alpha", text="alpha learned X"))
    beta.remember(SummaryMemory(agent_id="beta", text="beta learned Y"))

    fake.copy_object(
        Bucket="archive-bucket",
        Key="cold/q3.parquet",
        CopySource={"Bucket": TEST_BUCKET, "Key": TEST_KEY},
    )

    copy_uri = "s3://archive-bucket/cold/q3.parquet"
    reader = S3Memory(uri=copy_uri, agent_id="gamma", raw=RawAnnotationClient(fake))
    recalled = reader.recall(include_all_agents=True)
    assert {m.record.agent_id for m in recalled} == {"alpha", "beta"}
    assert {m.record.text for m in recalled} == {"alpha learned X", "beta learned Y"}


def test_d2_delete_destroys_only_its_annotations(alpha, fake):
    """Deleting an object removes its annotations; other objects are untouched."""
    alpha.remember(SummaryMemory(agent_id="alpha", text="on the doomed object"))

    other_uri = "s3://research-bucket/reports/2026/q4.parquet"
    fake.put_object(Bucket=TEST_BUCKET, Key="reports/2026/q4.parquet")
    other = S3Memory(uri=other_uri, agent_id="alpha", raw=alpha._raw)
    other.remember(SummaryMemory(agent_id="alpha", text="on the survivor"))

    fake.delete_object(Bucket=TEST_BUCKET, Key=TEST_KEY)

    # survivor still has its memory
    survived = other.recall()
    assert [m.record.text for m in survived] == ["on the survivor"]
    # doomed object is gone entirely
    assert (TEST_BUCKET, TEST_KEY) not in fake._objects


def test_d3_recall_text_bounds_and_content(alpha, beta, fake):
    alpha.remember(SummaryMemory(agent_id="alpha", text="alpha insight"))
    beta.remember(FindingMemory(agent_id="beta", text="beta found something", confidence=0.9))
    # plant an invalid memory to confirm it is flagged in the rendered text
    fake.plant_raw_annotation(TEST_BUCKET, TEST_KEY, "mem.beta.summary.bad", b"garbage")

    text = alpha.recall_text(max_chars=4000, include_all_agents=True)
    assert len(text) <= 4000
    # every memory names its agent_id and kind
    for m in alpha.recall(include_all_agents=True):
        assert m.agent_id in text
        assert m.kind in text
    assert "INVALID" in text
