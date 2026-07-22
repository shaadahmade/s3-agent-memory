"""Exception hierarchy for s3-agent-memory.

Every error the library raises intentionally derives from :class:`S3MemoryError`
so callers can catch the whole surface with one ``except`` while still being able
to distinguish specific failure modes.
"""

from __future__ import annotations


class S3MemoryError(Exception):
    """Base class for every error raised by this library."""


class AnnotationAPIUnsupported(S3MemoryError):
    """The supplied boto3/S3 client does not expose the annotation operations.

    Raised at construction time by :class:`s3_agent_memory.raw.RawAnnotationClient`
    when the client is missing ``put_object_annotation`` and friends (i.e. an
    older botocore that predates the June 2026 S3 annotations launch).
    """


class MemoryTooLarge(S3MemoryError):
    """A serialized memory payload exceeds the 1 MB per-annotation S3 limit."""


class AnnotationLimitExceeded(S3MemoryError):
    """The object already holds the maximum of 1,000 annotations."""


class AnnotationNotFound(S3MemoryError):
    """A named annotation/memory does not exist on the target object."""
