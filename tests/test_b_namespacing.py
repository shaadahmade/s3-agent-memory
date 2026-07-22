"""Gate B — namespacing & multi-agent safety."""

from __future__ import annotations

from s3_agent_memory import FindingMemory, SummaryMemory

from .conftest import TEST_BUCKET, TEST_KEY


def test_b1_cannot_forge_foreign_namespace(alpha, fake):
    """alpha writing a record forged as beta still lands under mem.alpha.*."""
    forged = SummaryMemory(agent_id="beta", text="I claim to be beta")
    name = alpha.remember(forged)

    assert name.startswith("mem.alpha.")
    stored_names = fake.annotation_names(TEST_BUCKET, TEST_KEY)
    assert stored_names == ["mem.alpha.summary"]
    # the persisted record's agent_id was overwritten too
    got = alpha.recall(kind="summary")
    assert got[0].record.agent_id == "alpha"


def test_b2_two_agents_no_clobber(alpha, beta):
    """Two agents writing the same kind produce two annotations, no clobbering."""
    alpha.remember(SummaryMemory(agent_id="alpha", text="alpha's view"))
    beta.remember(SummaryMemory(agent_id="beta", text="beta's view"))

    both = alpha.recall(kind="summary", include_all_agents=True)
    texts = {m.record.agent_id: m.record.text for m in both}
    assert texts == {"alpha": "alpha's view", "beta": "beta's view"}


def test_b3_slots_coexist_same_slot_lww(alpha):
    """Different slots coexist; same slot is documented last-writer-wins."""
    alpha.remember(SummaryMemory(agent_id="alpha", text="slot A"), slot="a")
    alpha.remember(SummaryMemory(agent_id="alpha", text="slot B"), slot="b")
    coexist = alpha.recall(kind="summary")
    assert {m.slot for m in coexist} == {"a", "b"}

    # same slot overwrites — this is intended behavior, not a bug.
    alpha.remember(SummaryMemory(agent_id="alpha", text="slot A v2"), slot="a")
    after = {m.slot: m.record.text for m in alpha.recall(kind="summary")}
    assert after == {"a": "slot A v2", "b": "slot B"}


def test_b4_recall_filtering(alpha, beta):
    alpha.remember(SummaryMemory(agent_id="alpha", text="a"))
    beta.remember(SummaryMemory(agent_id="beta", text="b"))

    all_mem = alpha.recall(kind="summary", include_all_agents=True)
    assert {m.agent_id for m in all_mem} == {"alpha", "beta"}

    only_beta = alpha.recall(kind="summary", agent_id="beta")
    assert [m.agent_id for m in only_beta] == ["beta"]

    default_self = alpha.recall(kind="summary")
    assert [m.agent_id for m in default_self] == ["alpha"]


def test_b5_foreign_annotations_ignored(alpha, fake):
    """Non-library annotations are ignored by recall and untouched by forget."""
    fake.plant_raw_annotation(TEST_BUCKET, TEST_KEY, "mediainfo", b"codec=h264")
    alpha.remember(SummaryMemory(agent_id="alpha", text="mine"))

    got = alpha.recall(include_all_agents=True)
    assert [m.name for m in got] == ["mem.alpha.summary"]

    alpha.forget("summary")  # deletes only the library memory
    remaining = fake.annotation_names(TEST_BUCKET, TEST_KEY)
    assert remaining == ["mediainfo"]  # foreign annotation survived


def test_b6_cannot_forget_another_agents_memory(alpha, beta, fake):
    """forget mutates only the caller's namespace — no back door on delete."""
    beta.remember(SummaryMemory(agent_id="beta", text="beta's, hands off"))

    # alpha has no 'summary' of its own, so forgetting raises rather than
    # reaching into beta's namespace.
    import pytest

    from s3_agent_memory import AnnotationNotFound

    with pytest.raises(AnnotationNotFound):
        alpha.forget("summary")

    # beta's memory is untouched.
    assert fake.annotation_names(TEST_BUCKET, TEST_KEY) == ["mem.beta.summary"]
