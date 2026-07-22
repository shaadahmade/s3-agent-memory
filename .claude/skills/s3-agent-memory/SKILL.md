---
name: s3-agent-memory
description: >-
  Give AI agents durable, object-attached memory using the s3-agent-memory
  library (Amazon S3 object annotations). Use this skill whenever the user wants
  agents to remember/recall knowledge that lives with an S3 object, sets up or
  uses the s3_agent_memory package, works with S3Memory / SummaryMemory /
  FindingMemory / FactMemory, wires memory tools into a LangGraph or LangChain
  agent, runs fleet-wide memory queries via Athena, or needs to try the library
  locally with no AWS. Triggers on "s3-agent-memory", "agent memory on S3", "S3
  annotations memory", "remember/recall for agents", "object-attached memory".
---

# s3-agent-memory

Durable, object-attached memory for AI agents. Memory is stored as **S3 object
annotations** attached to a specific `s3://bucket/key` object: it travels with
the object on copy/replication and is destroyed with it. No side database.

Use this skill to help the user install, configure, and use the library, or to
write application code on top of it.

## 0. Orient first

Before writing code, confirm the environment:

```bash
python -m pip show s3-agent-memory        # installed?
python -c "import boto3, botocore; print(boto3.__version__)"
```

- The library needs boto3/botocore that exposes the S3 annotation ops
  (`put/get/list/delete_object_annotation`), i.e. `boto3>=1.43`. If a supplied
  client lacks them, `RawAnnotationClient(...)` raises `AnnotationAPIUnsupported`
  at construction. Never work around this by mocking the ops into success.
- If the package is not installed, from the repo root run
  `pip install -e ".[dev]"` (add `.[langgraph]` for the agent tools).

## 1. Mental model (do not skip)

- Memory attaches to ONE object. An `S3Memory` binds one `agent_id` to one
  `s3://bucket/key`.
- Annotation names follow `mem.{agent_id}.{kind}[.{slot}]`.
- An instance may **write only** under its own `agent_id`, but may **read all**
  agents' memories on the object. A forged `record.agent_id` is overwritten on
  write. `forget()` can delete only your own memories.
- `agent_id` and `slot` must match `[A-Za-z0-9_-]+` (no dots, no whitespace).
  `kind` comes from the record type (`summary`, `finding`, `fact`).
- Same `(kind, slot)` written twice is last-writer-wins. Use distinct `slot`
  values when memories of the same kind must coexist.
- Hard limits per object: 1,000 annotations, 1 MB each. Oversized payloads raise
  `MemoryTooLarge` before any S3 call.

## 2. Two ways to run

**Real AWS** — pass a real boto3 client:

```python
import boto3
from s3_agent_memory import S3Memory, RawAnnotationClient

raw = RawAnnotationClient(boto3.client("s3"))
mem = S3Memory(uri="s3://my-bucket/reports/q3.parquet", agent_id="analyst-a", raw=raw)
```

Required IAM: `s3:PutObjectAnnotation`, `s3:GetObjectAnnotation` (plus the
list/delete counterparts your account models). For the fleet-query path: S3
Metadata annotation tables + Athena.

**Local, no AWS** — use the in-memory fake backend (great for demos/tests):

```python
from tests.fake_s3 import FakeS3
from s3_agent_memory import S3Memory, RawAnnotationClient

s3 = FakeS3()
s3.put_object(Bucket="b", Key="k")            # the object must exist first
raw = RawAnnotationClient(s3)
mem = S3Memory(uri="s3://b/k", agent_id="analyst-a", raw=raw)
```

To see the whole story end to end: `python examples/demo_two_agents.py`.

## 3. Core API

```python
from s3_agent_memory import SummaryMemory, FindingMemory, FactMemory

# WRITE — returns the annotation name; record.agent_id is forced to the instance's
mem.remember(SummaryMemory(agent_id="analyst-a", text="EMEA undercounted ~4%",
                           related_uris=["s3://my-bucket/reports/q2.parquet"]))
mem.remember(FindingMemory(agent_id="analyst-a", text="row 8842 null currency",
                           confidence=0.95), slot="row-8842")
mem.remember(FactMemory(agent_id="analyst-a", key="row_count", value="41210"))

# READ (always fresh, direct GET) -> list[RecalledMemory]
for m in mem.recall(include_all_agents=True):
    if m.invalid:
        print("bad record:", m.error)          # never silently dropped
    else:
        print(m.agent_id, m.kind, m.slot, m.record)

# READ as one text block for an LLM prompt (<= max_chars, tags each memory)
prompt_context = mem.recall_text(include_all_agents=True, max_chars=4000)

# DELETE one of your own memories (raises AnnotationNotFound if absent)
mem.forget("finding", slot="row-8842")
```

Signatures:
- `remember(record, slot=None) -> str`
- `recall(kind=None, *, agent_id=None, include_all_agents=False) -> list[RecalledMemory]`
- `recall_text(max_chars=4000, *, kind=None, agent_id=None, include_all_agents=True) -> str`
- `forget(kind, slot=None) -> None`

`RecalledMemory` fields: `name, agent_id, kind, slot, record, raw, invalid, error`.
When `invalid` is `True`, `record` is `None` and `error` says why.

Recall filtering:
- default: only THIS agent's memories.
- `include_all_agents=True`: every agent on the object.
- `agent_id="beta"`: isolate one agent.
- `kind="summary"`: restrict to one kind.

## 4. LangGraph / LangChain tools

```python
from s3_agent_memory import build_memory_tools

tools = build_memory_tools(mem)               # bound to mem's agent namespace
tools.remember_finding("null currency in row 8842", confidence=0.95,
                       related_uris="s3://a/b, s3://c/d")   # CSV -> clean list
tools.recall_memories()                       # returns a plain string, not a dict

lc_tools = tools.as_langchain_tools()         # StructuredTool[] for a LangGraph agent
# needs the [langgraph] extra; otherwise use tools.as_callables()
```

## 5. Fleet-wide query (Athena, ~1 hour lag)

Cross-object questions read the S3 Metadata annotation table, which lags live
writes by ~1 hour. NEVER use this for read-your-writes; use `S3Memory.recall`
for fresh reads. Inject any runner with a `run(sql) -> list[dict]` method.

```python
from s3_agent_memory import AthenaMemoryQuery
q = AthenaMemoryQuery(my_runner, table="s3_annotations")
q.objects_with_memory_kind("finding")         # objects carrying any 'finding'
q.search_memory_text("currency")              # single quotes are escaped for you
q.memories_for_agent("analyst-a")
```

The library ships the SQL builder + escaping, not a live Athena executor. If the
user needs one, implement a runner using boto3 `athena` (start_query_execution ->
poll -> get_query_results) and pass it in.

## 6. Errors to handle

- `MemoryTooLarge` — payload > 1 MB. Remediation: split across `slot=` values, or
  store the bulk in a separate S3 object and keep only a pointer URI in the memory.
- `AnnotationLimitExceeded` — object already has 1,000 annotations.
- `AnnotationNotFound` — `forget()` of a memory that does not exist.
- `AnnotationAPIUnsupported` — boto3 too old / client lacks the annotation ops.
- `ValueError` — malformed `s3://` URI or bad `agent_id`/`slot` charset.

All derive from `S3MemoryError`, so `except S3MemoryError` catches the whole
surface.

## 7. When NOT to use this

Steer the user elsewhere if they want: hot per-turn conversational memory;
sub-second multi-hop retrieval; vector / semantic similarity search; or
read-your-writes across the fleet (the Athena path lags ~1 hour). This is for
durable knowledge that should live and die with the data it describes.

## 8. Verify your work

After writing code against the library, run the suite from the repo root:

```bash
pytest -q            # expect: 24 passed, 3 skipped (gate G is live-AWS, env-gated)
```

Gate G runs only when `S3MEM_LIVE_BUCKET` is set and boto3 supports the ops;
otherwise it is correctly SKIPPED, never reported as passed.
