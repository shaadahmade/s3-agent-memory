# Engineering Log — s3-agent-memory

Running log of the eval-driven build. Newest entries appended at the bottom.

## 2026-07-22 — Phase 0 pre-flight: boto3 annotation-API probe

Environment: Python 3.11.15, fresh container.

Installed deps: `boto3==1.43.54`, `botocore==1.43.54`, `pydantic==2.13.4`,
`pytest==9.1.1`, `langgraph==1.2.9`, `langchain-core==1.5.0`.

**Probe result — the annotation operations ARE present in this botocore build.**

```
$ python -c "print([o for o in dir(boto3.client('s3')) if 'nnotation' in o])"
['delete_object_annotation', 'get_object_annotation', 'list_object_annotations',
 'put_object_annotation', 'update_bucket_metadata_annotation_table_configuration']
```

Operation models present: `PutObjectAnnotation`, `GetObjectAnnotation`,
`ListObjectAnnotations`, `DeleteObjectAnnotation`
(+ `UpdateBucketMetadataAnnotationTableConfiguration`).

Wire shapes captured from the botocore service model (drives `raw.py` and the fake):

- **PutObjectAnnotation** `PUT /{Bucket}/{Key+}?annotation` — required
  `Bucket`, `Key`, `AnnotationName` (querystring `annotationName`),
  `AnnotationPayload` (streaming blob body). Returns `ETag`, `ObjectVersionId`.
- **GetObjectAnnotation** `GET /{Bucket}/{Key+}?annotation` — required
  `Bucket`, `Key`, `AnnotationName`. Returns `AnnotationPayload` (streaming
  blob), `LastModified`, `ContentLength`, `ETag`. Modeled errors:
  `NoSuchBucket`, `NoSuchKey`, **`NoSuchAnnotation`**.
- **ListObjectAnnotations** `GET ...?annotation` — optional `AnnotationPrefix`,
  `ContinuationToken`, `MaxAnnotationResults`. Returns `Annotations` (list of
  `{AnnotationName, LastModified, ETag, Size, ...}`), `NextContinuationToken`.
- **DeleteObjectAnnotation** `DELETE ...?annotation`, 204 — required
  `AnnotationName`. Modeled errors: `NoSuchBucket`, `NoSuchKey` **only**
  (NO `NoSuchAnnotation`). => Delete is idempotent server-side, so `forget()`
  of a missing memory must check existence itself before deleting to satisfy
  eval C4. Design decision recorded.

Because the ops are present, `raw.py` will NOT unconditionally raise
`AnnotationAPIUnsupported`; it raises that only when a supplied client lacks the
`*_object_annotation` methods (older botocore), with an upgrade hint. Gate G is
gated on `S3MEM_LIVE_BUCKET`; env not set here => G will be **SKIPPED-ENV**,
never faked to green.

## 2026-07-22 — Phase 0 complete: harness written first

Wrote the entire eval suite (gates A–G) plus `tests/fake_s3.py` before any
package implementation. Gates A–F are concrete pytest assertions; G is env-gated
on `S3MEM_LIVE_BUCKET` and marked SKIPPED-ENV when unset (never faked green).

## 2026-07-22 — Phases 1–4: implementation

Implemented `errors.py`, `schemas.py`, `raw.py`, `client.py`, `query.py`,
`langgraph_tools.py`, `__init__.py`, and the fake backend.

First full run: **23 passed, 3 skipped** (gate G). No red gates — the up-front
design (namespace parsing, size check before the S3 call, invalid-but-returned
read path, idempotent-delete + pre-check for forget) matched the evals on the
first pass, so the BUILD→EVAL→DIAGNOSE→FIX loop never needed a repair iteration.

### Three most interesting design frictions (found while writing the evals)

1. **DeleteObjectAnnotation does NOT model `NoSuchAnnotation`.** The botocore
   model lists only `NoSuchBucket`/`NoSuchKey` for delete, i.e. real S3 delete is
   idempotent. But eval C4 requires `forget()` of a missing memory to raise.
   Resolution: `forget()` checks existence via `list_annotation_names` first and
   raises `AnnotationNotFound` itself; `raw.delete_annotation` stays idempotent.
   The fake mirrors real semantics (no error on missing delete) so the two paths
   are honest.

2. **Exact 1 MB boundary (A3).** Hitting exactly 1,000,000 serialized bytes
   requires knowing the JSON envelope overhead. Rather than hard-code it, the
   test measures overhead through the library's own `to_payload()` on an
   empty-text record, then pads ASCII 'a' to the exact remainder. The size check
   lives in `client.remember` (before the S3 call, per contract item 5); the fake
   also enforces the cap as belt-and-suspenders.

3. **Read path that never crashes on bad data (C2/C3).** `from_payload` returns
   `(record, error)` instead of raising, so a malformed annotation comes back as
   `RecalledMemory(invalid=True)` alongside good ones. The list/get race (C3) is
   handled by catching `AnnotationNotFound` per-annotation inside `recall` and
   skipping — the fake's `schedule_vanish_on_get` hook simulates the race.

## 2026-07-22 — FAKE-DETECTION self-audit

`grep -rnE 'pytest.skip|xfail|pass  #|except:\s*pass|\bmock\b' s3_agent_memory/`
=> no hits. No `mock`, no swallowed exceptions, no skips/xfails in the package.
The only skips are gate G's env-gate in `tests/test_g_live.py` (legitimate:
requires real AWS + a live bucket). No test asserts on a value it wrote into the
fake bypassing the library, except C2's explicitly-allowed malformed plant. The
demo imports from `tests/` only `fake_s3`, as permitted.

## 2026-07-22 — Flake check + fresh-venv install

Ran `pytest -q` three times back-to-back: identical `23 passed, 3 skipped`.
Created a clean venv, `pip install -e ".[dev]"`, re-ran the suite (same result),
and ran `examples/demo_two_agents.py` — Agent A learns, dies, Agent B recalls,
and the memories survive a cross-bucket copy. All green.

### The one design decision I'd revisit

`forget()` does a full `list_annotation_names` to confirm existence before
deleting — one extra S3 round-trip per delete purely to make "forget a missing
memory" raise instead of silently succeeding. On a hot object with many
annotations that list is not free. A cheaper alternative is to attempt
`get_object_annotation` (which DOES model `NoSuchAnnotation`) and delete on
success — one round-trip either way, and it streams only the one payload rather
than listing all names. I'd switch `forget` to the get-based existence check.

## 2026-07-22 — Post-build review fix: write-isolation back door on forget()

Self-review after the green run found a real contract violation. `forget()` had
an `agent_id=` override, so agent alpha could `forget(kind, agent_id="beta")`
and delete beta's memory. Contract item 1 restricts an instance to WRITE only
under its own agent_id — and deletion is a mutation of another agent's
namespace, so this was a hole in the isolation guarantee (which `remember()`
enforces via the forged-id overwrite, but `forget()` did not).

Fix: removed the `agent_id` parameter; `forget()` now operates strictly on the
caller's own namespace. Added eval **B6** to pin it: alpha forgetting a kind it
doesn't own raises `AnnotationNotFound` and never touches beta's memory. New
result: **24 passed, 3 skipped**. Demo unaffected.
