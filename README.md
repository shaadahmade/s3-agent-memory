# s3-agent-memory

Durable memory for AI agents, stored directly on your Amazon S3 objects.

Instead of keeping an agent's knowledge in a separate database that you have to
run, sync, and back up, this library attaches memories to the S3 object they are
about, using S3 object annotations. A memory travels with the object when it is
copied and is deleted when the object is deleted. When an agent finishes and a
new one starts later, it reads the memories straight off the object and picks up
where the last one left off.

Each memory is a small validated record (a summary, a finding, or a fact) stored
under a name like `mem.{agent_id}.{kind}`. An agent can write only under its own
name but can read every agent's memories on the object.

## Install

```bash
pip install -e ".[dev]"
```

Add the LangGraph tools with:

```bash
pip install -e ".[langgraph]"
```

## Requirements

- Python 3.10 or newer.
- `boto3` 1.43 or newer (it must expose the S3 object annotation operations).
- An S3 bucket, with IAM permissions `s3:PutObjectAnnotation` and
  `s3:GetObjectAnnotation` (plus the list and delete counterparts).

## Quick start

```python
import boto3
from s3_agent_memory import S3Memory, RawAnnotationClient, SummaryMemory

# Point a memory at one S3 object, as one agent.
raw = RawAnnotationClient(boto3.client("s3"))
mem = S3Memory(
    uri="s3://my-bucket/reports/q3.parquet",
    agent_id="analyst-a",
    raw=raw,
)

# Write a memory attached to that object.
mem.remember(SummaryMemory(
    agent_id="analyst-a",
    text="EMEA revenue looks undercounted by about 4 percent.",
))

# Later, a completely separate agent reads it back off the same object.
next_agent = S3Memory(
    uri="s3://my-bucket/reports/q3.parquet",
    agent_id="analyst-b",
    raw=raw,
)
print(next_agent.recall_text(include_all_agents=True))
```

## Writing memories

There are three memory types. Every write is validated before it is stored.

```python
from s3_agent_memory import SummaryMemory, FindingMemory, FactMemory

mem.remember(SummaryMemory(agent_id="analyst-a", text="Q3 undercounts EMEA."))
mem.remember(FindingMemory(agent_id="analyst-a", text="Row 8842 has no currency.",
                           confidence=0.95))
mem.remember(FactMemory(agent_id="analyst-a", key="row_count", value="41210"))
```

To keep several memories of the same kind on one object, give them different
slots. Writing the same kind and slot again replaces the previous value.

```python
mem.remember(SummaryMemory(agent_id="analyst-a", text="North region"), slot="north")
mem.remember(SummaryMemory(agent_id="analyst-a", text="South region"), slot="south")
```

A memory must serialize to 1 MB or less. Larger payloads raise `MemoryTooLarge`.

## Reading memories

`recall` returns the memories on the object. By default it returns only your
own; pass `include_all_agents=True` for everyone, or `agent_id="beta"` for one
agent. You can also filter by `kind`.

```python
for m in mem.recall(include_all_agents=True):
    if m.invalid:
        print("could not read a record:", m.error)
    else:
        print(m.agent_id, m.kind, m.record)
```

`recall_text` returns the same memories as a single text block you can drop into
an LLM prompt.

```python
context = mem.recall_text(include_all_agents=True, max_chars=4000)
```

## Deleting memories

`forget` removes one of your own memories. It raises `AnnotationNotFound` if the
memory does not exist.

```python
mem.forget("finding", slot="row-8842")
```

## Using it inside a LangGraph or LangChain agent

```python
from s3_agent_memory import build_memory_tools

tools = build_memory_tools(mem)

# Plain callables:
tools.remember_finding("Row 8842 has no currency.", confidence=0.95,
                       related_uris="s3://a/b, s3://c/d")
print(tools.recall_memories())

# Or as LangChain StructuredTools for a LangGraph agent:
lc_tools = tools.as_langchain_tools()
```

## Trying it without AWS

The project ships an in-memory S3 backend so you can run everything with no
credentials. The included demo shows one agent writing memories, ending, and a
second agent recalling them:

```bash
python examples/demo_two_agents.py
```

## License

MIT. See `LICENSE`.
