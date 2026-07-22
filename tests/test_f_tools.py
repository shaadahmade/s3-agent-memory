"""Gate F — agent-tool layer."""

from __future__ import annotations

from s3_agent_memory import build_memory_tools, parse_uri_list

from .conftest import TEST_BUCKET, TEST_KEY


def test_f1_recall_tool_returns_string(alpha):
    tools = build_memory_tools(alpha)
    saved = tools.remember_finding("something worth keeping")
    assert isinstance(saved, str)

    out = tools.recall_memories()
    assert isinstance(out, str)
    # a real string block, not a Python dict/list repr
    assert not out.startswith(("{", "["))
    assert "something worth keeping" in out


def test_f2_related_uris_parsing():
    assert parse_uri_list("a, b ,c") == ["a", "b", "c"]
    assert parse_uri_list("  s3://x/y ,, s3://z/w ,") == ["s3://x/y", "s3://z/w"]
    assert parse_uri_list("") == []
    assert parse_uri_list(None) == []
    assert parse_uri_list(["p", " q "]) == ["p", "q"]


def test_f3_tools_write_under_bound_agent(fake, raw):
    from tests.conftest import make_memory

    alpha_mem = make_memory(raw, "alpha")
    tools = build_memory_tools(alpha_mem)
    tools.remember_finding("via tool", related_uris="s3://a/b, s3://c/d")

    names = fake.annotation_names(TEST_BUCKET, TEST_KEY)
    assert names == ["mem.alpha.finding"]
    # and the parsed URIs made it onto the record
    got = alpha_mem.recall(kind="finding")
    assert got[0].record.related_uris == ["s3://a/b", "s3://c/d"]
