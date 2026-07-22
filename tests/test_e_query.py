"""Gate E — fleet query layer (SQL is asserted; no live Athena)."""

from __future__ import annotations

import inspect

from s3_agent_memory import AthenaMemoryQuery


class RecordingRunner:
    """Captures the SQL it is asked to run and returns canned rows."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries: list[str] = []

    def run(self, sql: str):
        self.queries.append(sql)
        return self.rows


def test_e1_objects_with_memory_kind_sql():
    runner = RecordingRunner()
    q = AthenaMemoryQuery(runner)
    q.objects_with_memory_kind("summary")
    sql = runner.queries[-1]
    assert "name LIKE 'mem.%.summary%'" in sql


def test_e2_search_escapes_single_quotes():
    runner = RecordingRunner()
    q = AthenaMemoryQuery(runner)
    q.search_memory_text("O'Brien'; DROP TABLE --")
    sql = runner.queries[-1]
    # the needle's single quotes are doubled, so it stays inside the literal
    assert "O''Brien''; DROP TABLE --" in sql
    # no un-doubled quote can terminate the literal early
    assert "'O'Brien'" not in sql


def test_e3_docstrings_disclose_lag():
    public = [
        name
        for name, _ in inspect.getmembers(AthenaMemoryQuery, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert public  # sanity: there are public methods
    for name in public:
        doc = getattr(AthenaMemoryQuery, name).__doc__ or ""
        assert "hour" in doc.lower(), f"{name} docstring must disclose the ~1h lag"
