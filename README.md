# s3-agent-memory

Durable, **object-attached** memory for AI agents, built on Amazon S3 object
annotations (launched June 2026).

An agent's knowledge about a piece of data should live *with that data* — not in
a side database you have to keep in sync, back up, and garbage-collect. S3
annotations are named, mutable metadata (up to 1,000 per object, ≤1 MB each) that
**travel with the object on copy/replication and are destroyed with it**. This
library turns that primitive into a small, honest memory API for agents.

```python
import boto3
from s3_agent_memory import S3Memory, RawAnnotationClient, SummaryMemory

raw = RawAnnotationClient(boto3.client("s3"))
mem = S3Memory(uri="s3://my-bucket/reports/q3.parquet", agent_id="analyst-a", raw=raw)

# Agent A writes a durable memory attached to the object...
mem.remember(SummaryMemory(agent_id="analyst-a",
                           text="EMEA revenue looks undercounted by ~4%."))

# ...a totally separate Agent B, later, on the same object, recalls it:
later = S3Memory(uri="s3://my-bucket/reports/q3.parquet", agent_id="analyst-b", raw=raw)
print(later.recall_text(include_all_agents=True))
# - [analyst-a/summary] EMEA revenue looks undercounted by ~4%.
```

Run the end-to-end story (no AWS needed, uses the in-memory fake backend):

```bash
python examples/demo_two_agents.py
```

## Install

```bash
pip install -e ".[dev]"     # library + test/dev extras
# optional LangGraph/LangChain tool integration:
pip install -e ".[langgraph]"
```

## Requirements

This library needs a `boto3`/`botocore` build that exposes the S3 annotation
operations — `PutObjectAnnotation`, `GetObjectAnnotation`,
`ListObjectAnnotations`, `DeleteObjectAnnotation` (botocore ≥ the June 2026
release; `boto3>=1.43` here). If you hand `RawAnnotationClient` a client that
lacks these methods, it raises `AnnotationAPIUnsupported` **at construction**
with an upgrade hint — the library never silently no-ops. IAM actions required:
`s3:PutObjectAnnotation`, `s3:GetObjectAnnotation` (plus the read/list
counterparts your account models).

## Design contract

1. **Namespacing.** Annotation names are `mem.{agent_id}.{kind}[.{slot}]`. An
   `S3Memory` may **write only** under its own `agent_id`, but may **read all**
   agents' memories on the object. A forged `record.agent_id` is overwritten on
   write — you cannot write into another agent's namespace.
2. **Validation.** Every write passes a Pydantic v2 schema; every read
   re-validates and surfaces bad records as `invalid=True` (with a reason) —
   never silently dropped, never crashing the whole recall.
3. **Isolated AWS surface.** Every raw boto3 annotation call lives in exactly one
   module, `raw.py`. Nothing else imports boto3 for annotations.
4. **Two honest read paths.** `S3Memory.recall` is a direct GET — always fresh.
   `AthenaMemoryQuery` is fleet-wide but reads the S3 Metadata annotation table,
   which lags live writes by **~1 hour**. The lag is stated in every query
   docstring; Athena results are never presented as fresh.
5. **Size safety.** Payloads over 1 MB are rejected with a clear `MemoryTooLarge`
   error and a remediation hint (use `slot=`, or store the bulk in a separate
   object and keep a pointer URI).
6. **No hidden infra.** S3 only (plus optional Athena). No DynamoDB, no Redis, no
   side database.

## Concurrency semantics (read this)

S3 annotation writes are **last-writer-wins** — there are no conditional puts on
the same annotation name. The namespacing scheme exists precisely to make
same-name concurrent writes structurally rare: two different agents never share a
name, and one agent serializes its own writes per `(kind, slot)`. Writing the
same `(kind, slot)` twice **intentionally** overwrites — use distinct `slot`
values when you want memories to coexist.

## When NOT to use this

This is durable, object-scoped memory. It is **the wrong tool** for:

- **Hot conversational memory / scratchpad.** Per-turn chat state churns far too
  fast; keep that in process or in a low-latency store.
- **Sub-second multi-hop retrieval.** Each recall is one or more S3 round-trips;
  it is not a graph engine.
- **Vector / semantic similarity search.** There is no embedding index here.
  Pair this with a real vector store if you need nearest-neighbor recall.
- **Read-your-writes across the fleet.** The Athena path lags ~1 hour. For fresh
  reads you must go through `S3Memory.recall` on the specific object.
- **Millions of tiny facts on one object.** The hard ceiling is 1,000
  annotations × 1 MB per object; design around it.

## Package layout

| Module | Responsibility |
| --- | --- |
| `raw.py` | The only boto3 annotation wrapper (put/get/list/delete). |
| `schemas.py` | Pydantic v2 memory types + the JSON payload envelope. |
| `client.py` | `S3Memory`: remember / recall / recall_text / forget (fresh). |
| `query.py` | `AthenaMemoryQuery`: fleet-wide SQL over the annotation table (~1h lag). |
| `langgraph_tools.py` | LangGraph/LangChain agent tools bound to an `S3Memory`. |
| `tests/fake_s3.py` | Faithful in-memory S3 annotation backend (a deliverable). |

## Eval results

Every gate below is implemented as pytest tests; a gate passes only when all its
tests pass. Gate G (live AWS) is env-gated on `S3MEM_LIVE_BUCKET` and is reported
`SKIPPED-ENV` when unset — never as passed.

| Gate | What it proves | Tests | Status |
| --- | --- | --- | --- |
| A | Round-trip integrity (deep equality, unicode, 1 MB boundary, 1,000-name cap) | 4 | **PASS** |
| B | Namespacing & multi-agent safety (no forging, no clobber, slots, filtering, foreign-annotation isolation, no cross-agent forget) | 6 | **PASS** |
| C | Robustness & failure honesty (empty, malformed-flagged, list/get race, forget-missing, bad URIs) | 5 | **PASS** |
| D | Durability semantics (annotations travel on copy, die on delete, `recall_text` bounds) | 3 | **PASS** |
| E | Fleet query layer (SQL shape, quote-escaping, lag disclosure) | 3 | **PASS** |
| F | Agent-tool layer (string output, URI parsing, namespace binding) | 3 | **PASS** |
| G | Live smoke against real AWS | 3 | **SKIPPED-ENV** (`S3MEM_LIVE_BUCKET` unset) |

Last pytest summary line:

```
24 passed, 3 skipped in 0.05s
```

## License

MIT — see `LICENSE`.
