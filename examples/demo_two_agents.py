"""Demo: Agent A learns something, dies, and Agent B recalls it off the object.

This is the whole thesis of the library in ~40 lines: the memory lives ON the S3
object, so a brand-new agent process with no shared state recovers everything its
predecessor knew — and it even survives the object being copied to cold storage.

Runs against the in-memory FakeS3 backend so it needs no AWS credentials. The
only thing it borrows from the test tree is the fake backend itself.

    python examples/demo_two_agents.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable so we can borrow the fake S3 backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fake_s3 import FakeS3  # noqa: E402  (only allowed tests import)

from s3_agent_memory import (  # noqa: E402
    FindingMemory,
    RawAnnotationClient,
    S3Memory,
    SummaryMemory,
    build_memory_tools,
)

URI = "s3://research-bucket/reports/2026/q3.parquet"
BUCKET, KEY = "research-bucket", "reports/2026/q3.parquet"


def main() -> None:
    s3 = FakeS3()
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=b"...the Q3 report...")
    raw = RawAnnotationClient(s3)

    # --- Agent A: does some analysis, writes durable memories, then "dies" ---
    print("Agent A is analyzing the object...")
    agent_a = S3Memory(uri=URI, agent_id="analyst-a", raw=raw)
    agent_a.remember(
        SummaryMemory(
            agent_id="analyst-a",
            text="EMEA revenue looks undercounted by ~4%; verify against Q2.",
            related_uris=["s3://research-bucket/reports/2026/q2.parquet"],
        )
    )
    # Tools write under agent A's namespace automatically.
    tools_a = build_memory_tools(agent_a)
    print("  ", tools_a.remember_finding(
        "Row 8842 has a null currency code.",
        confidence=0.95,
        related_uris="s3://research-bucket/reports/2026/q3.parquet",
    ))
    del agent_a, tools_a  # Agent A's process ends. No shared state survives.
    print("Agent A has died. Nothing kept in memory.\n")

    # --- Agent B: a fresh agent, no shared state, recalls off the object ---
    print("Agent B wakes up on the same object with zero prior context...")
    agent_b = S3Memory(uri=URI, agent_id="analyst-b", raw=raw)
    print(agent_b.recall_text(include_all_agents=True))
    print()

    # --- Durability: the object is archived to a different bucket ---
    s3.copy_object(
        Bucket="archive-bucket",
        Key="cold/q3.parquet",
        CopySource={"Bucket": BUCKET, "Key": KEY},
    )
    archived = S3Memory(
        uri="s3://archive-bucket/cold/q3.parquet",
        agent_id="analyst-c",
        raw=RawAnnotationClient(s3),
    )
    recalled = archived.recall(include_all_agents=True)
    print(f"After copy to cold storage, the copy still carries {len(recalled)} "
          "memories:")
    for m in recalled:
        flag = " [INVALID]" if m.invalid else ""
        print(f"  - {m.agent_id}/{m.kind}{flag}: "
              f"{getattr(m.record, 'text', '?')}")


if __name__ == "__main__":
    main()
