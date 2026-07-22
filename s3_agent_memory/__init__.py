"""s3-agent-memory: durable, object-attached memory for AI agents on Amazon S3.

Built on the S3 object annotations feature (June 2026): named, mutable metadata
that travels with an object on copy/replication and is destroyed with it. This
gives agents memory whose lifecycle is exactly the lifecycle of the data it is
about — no side database to keep in sync.

Public surface:
    S3Memory            per-object, per-agent memory; fresh reads (direct GET)
    AthenaMemoryQuery   fleet-wide queries over the metadata table (~1h lag)
    RawAnnotationClient the sole boto3 annotation wrapper
    MemoryToolset       LangGraph/LangChain agent tools
    schemas             SummaryMemory, FindingMemory, FactMemory, ...
"""

from __future__ import annotations

from .client import RecalledMemory, S3Memory, parse_s3_uri
from .errors import (
    AnnotationAPIUnsupported,
    AnnotationLimitExceeded,
    AnnotationNotFound,
    MemoryTooLarge,
    S3MemoryError,
)
from .langgraph_tools import MemoryToolset, build_memory_tools, parse_uri_list
from .query import AthenaMemoryQuery
from .raw import RawAnnotationClient
from .schemas import (
    MAX_ANNOTATION_BYTES,
    MEMORY_TYPES,
    FactMemory,
    FindingMemory,
    MemoryRecord,
    SummaryMemory,
    from_payload,
    to_payload,
)

__version__ = "0.1.0"

__all__ = [
    "S3Memory",
    "RecalledMemory",
    "parse_s3_uri",
    "AthenaMemoryQuery",
    "RawAnnotationClient",
    "MemoryToolset",
    "build_memory_tools",
    "parse_uri_list",
    "MemoryRecord",
    "SummaryMemory",
    "FindingMemory",
    "FactMemory",
    "MEMORY_TYPES",
    "MAX_ANNOTATION_BYTES",
    "to_payload",
    "from_payload",
    "S3MemoryError",
    "AnnotationAPIUnsupported",
    "AnnotationLimitExceeded",
    "AnnotationNotFound",
    "MemoryTooLarge",
]
