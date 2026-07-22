"""AthenaMemoryQuery — the fleet-wide read path (eventually consistent).

This layer queries the S3 Metadata **annotation table** (a managed Iceberg table
populated from object annotations) via Athena. It answers questions across many
objects at once — "which objects has any agent summarized?" — that the per-object
:class:`s3_agent_memory.client.S3Memory` cannot.

Honesty note (contract item 4): the annotation table lags roughly **one hour**
behind live writes. These methods are for fleet analytics, NOT for reading a
memory you wrote seconds ago. For fresh reads, use ``S3Memory.recall``.

The Athena runner is injected so the SQL can be unit-tested without AWS: pass any
object with ``run(sql: str) -> list[dict]``.
"""

from __future__ import annotations

from typing import Any, Protocol


class AthenaRunner(Protocol):
    """Anything that can execute a SQL string and return rows."""

    def run(self, sql: str) -> list[dict[str, Any]]: ...


def _sql_quote(value: str) -> str:
    """Escape a string for safe single-quoted inclusion in an SQL literal.

    Doubles embedded single quotes (standard SQL) so needles like
    ``O'Brien'; DROP TABLE --`` cannot break out of the literal (eval E2).
    """
    return value.replace("'", "''")


class AthenaMemoryQuery:
    """Fleet-wide queries over the S3 Metadata annotation table (~1h lag)."""

    def __init__(self, runner: AthenaRunner, table: str = "s3_annotations"):
        self._runner = runner
        # Table name is a trusted config value, not user input; still constrain it.
        if not table.replace("_", "").replace(".", "").isalnum():
            raise ValueError(f"unsafe table identifier: {table!r}")
        self.table = table

    def objects_with_memory_kind(self, kind: str) -> list[dict[str, Any]]:
        """List objects carrying any memory of ``kind``, fleet-wide.

        Results reflect the annotation table, which trails live writes by about
        one hour; a memory written minutes ago may not appear yet.
        """
        needle = _sql_quote(kind)
        sql = (
            f"SELECT bucket, key, name, text_value "
            f"FROM {self.table} "
            f"WHERE name LIKE 'mem.%.{needle}%'"
        )
        return self._runner.run(sql)

    def search_memory_text(self, needle: str) -> list[dict[str, Any]]:
        """Full-text search over memory payloads across the fleet.

        Matches on the annotation table's ``text_value`` column. Data lags live
        writes by roughly one hour, so this is for retrospective search, not
        read-your-writes.
        """
        safe = _sql_quote(needle)
        sql = (
            f"SELECT bucket, key, name, text_value "
            f"FROM {self.table} "
            f"WHERE name LIKE 'mem.%' AND text_value LIKE '%{safe}%'"
        )
        return self._runner.run(sql)

    def memories_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """List every memory a given agent has written, fleet-wide.

        Backed by the annotation table (~one hour of lag behind live writes).
        """
        safe = _sql_quote(agent_id)
        sql = (
            f"SELECT bucket, key, name, text_value "
            f"FROM {self.table} "
            f"WHERE name LIKE 'mem.{safe}.%'"
        )
        return self._runner.run(sql)
