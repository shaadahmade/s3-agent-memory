"""Shared pytest fixtures for the eval suite."""

from __future__ import annotations

import pytest

from s3_agent_memory import RawAnnotationClient, S3Memory

from .fake_s3 import FakeS3

TEST_URI = "s3://research-bucket/reports/2026/q3.parquet"
TEST_BUCKET = "research-bucket"
TEST_KEY = "reports/2026/q3.parquet"


@pytest.fixture
def fake() -> FakeS3:
    s3 = FakeS3()
    s3.put_object(Bucket=TEST_BUCKET, Key=TEST_KEY, Body=b"...data...")
    return s3


@pytest.fixture
def raw(fake: FakeS3) -> RawAnnotationClient:
    return RawAnnotationClient(fake)


def make_memory(raw: RawAnnotationClient, agent_id: str, uri: str = TEST_URI) -> S3Memory:
    return S3Memory(uri=uri, agent_id=agent_id, raw=raw)


@pytest.fixture
def alpha(raw: RawAnnotationClient) -> S3Memory:
    return make_memory(raw, "alpha")


@pytest.fixture
def beta(raw: RawAnnotationClient) -> S3Memory:
    return make_memory(raw, "beta")
