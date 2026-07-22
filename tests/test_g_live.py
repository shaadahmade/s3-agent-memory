"""Gate G — live smoke against real AWS. Env-gated; NEVER faked.

Runs only when ``S3MEM_LIVE_BUCKET`` is set AND the installed boto3 exposes the
annotation operations. Otherwise every test is SKIPPED-ENV — it is never
reported as passed. Each test cleans up after itself (G3).
"""

from __future__ import annotations

import os
import uuid

import pytest

LIVE_BUCKET = os.environ.get("S3MEM_LIVE_BUCKET")


def _boto3_supports_annotations() -> bool:
    try:
        import boto3

        client = boto3.client("s3")
        return all(
            hasattr(client, m)
            for m in (
                "put_object_annotation",
                "get_object_annotation",
                "list_object_annotations",
                "delete_object_annotation",
            )
        )
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not LIVE_BUCKET or not _boto3_supports_annotations(),
    reason="SKIPPED-ENV: set S3MEM_LIVE_BUCKET and use a boto3 with annotation ops",
)


@pytest.fixture(scope="module")
def live_s3():
    import boto3

    return boto3.client("s3")


def test_g1_roundtrip_live(live_s3):
    import boto3

    from s3_agent_memory import RawAnnotationClient, S3Memory, SummaryMemory

    key = f"s3mem-live/{uuid.uuid4()}.bin"
    live_s3.put_object(Bucket=LIVE_BUCKET, Key=key, Body=b"live-smoke")
    try:
        mem = S3Memory(
            uri=f"s3://{LIVE_BUCKET}/{key}",
            agent_id="livealpha",
            raw=RawAnnotationClient(live_s3),
        )
        mem.remember(SummaryMemory(agent_id="livealpha", text="live round-trip"))
        got = mem.recall(kind="summary")
        assert got and got[0].record.text == "live round-trip"
    finally:
        _cleanup(live_s3, key)


def test_g2_copy_carries_annotations_live(live_s3):
    from s3_agent_memory import RawAnnotationClient, S3Memory, SummaryMemory

    src_key = f"s3mem-live/{uuid.uuid4()}.bin"
    dst_key = f"s3mem-live/{uuid.uuid4()}.copy.bin"
    live_s3.put_object(Bucket=LIVE_BUCKET, Key=src_key, Body=b"src")
    try:
        mem = S3Memory(
            uri=f"s3://{LIVE_BUCKET}/{src_key}",
            agent_id="livealpha",
            raw=RawAnnotationClient(live_s3),
        )
        mem.remember(SummaryMemory(agent_id="livealpha", text="carried on copy"))
        live_s3.copy_object(
            Bucket=LIVE_BUCKET,
            Key=dst_key,
            CopySource={"Bucket": LIVE_BUCKET, "Key": src_key},
        )
        copy_mem = S3Memory(
            uri=f"s3://{LIVE_BUCKET}/{dst_key}",
            agent_id="livebeta",
            raw=RawAnnotationClient(live_s3),
        )
        got = copy_mem.recall(include_all_agents=True)
        assert any(m.record and m.record.text == "carried on copy" for m in got)
    finally:
        _cleanup(live_s3, src_key)
        _cleanup(live_s3, dst_key)


def test_g3_cleanup_is_verifiable(live_s3):
    """Sanity: after cleanup, the object is gone (G3's guarantee, checked)."""
    key = f"s3mem-live/{uuid.uuid4()}.bin"
    live_s3.put_object(Bucket=LIVE_BUCKET, Key=key, Body=b"tmp")
    _cleanup(live_s3, key)
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        live_s3.head_object(Bucket=LIVE_BUCKET, Key=key)


def _cleanup(client, key: str) -> None:
    try:
        client.delete_object(Bucket=LIVE_BUCKET, Key=key)
    except Exception:
        pass
