"""The ONE module that touches the boto3 S3 annotation surface.

Contract item 3: nothing else in the package imports boto3 for annotations.
Everything above this layer speaks in ``(bucket, key, name, bytes)`` and never
sees a ``ClientError`` or a ``StreamingBody``.

The client passed in only needs to quack like the annotation subset of a boto3
S3 client, so the in-memory fake used in tests is a drop-in substitute.
"""

from __future__ import annotations

from typing import Any, Protocol

from .errors import (
    AnnotationAPIUnsupported,
    AnnotationLimitExceeded,
    AnnotationNotFound,
    MemoryTooLarge,
)

# Required annotation methods a client must expose to be usable here.
_REQUIRED_METHODS = (
    "put_object_annotation",
    "get_object_annotation",
    "list_object_annotations",
    "delete_object_annotation",
)

_UPGRADE_HINT = (
    "This boto3/botocore build predates the June 2026 S3 annotations launch. "
    "Upgrade with `pip install -U 'boto3>=1.43'` (or supply a client that "
    "implements put/get/list/delete_object_annotation)."
)


class SupportsAnnotations(Protocol):
    """Structural type for the client RawAnnotationClient wraps."""

    def put_object_annotation(self, **kwargs: Any) -> Any: ...
    def get_object_annotation(self, **kwargs: Any) -> Any: ...
    def list_object_annotations(self, **kwargs: Any) -> Any: ...
    def delete_object_annotation(self, **kwargs: Any) -> Any: ...


def _client_error_class():
    """Import botocore's ClientError lazily; return None if botocore absent."""
    try:
        from botocore.exceptions import ClientError

        return ClientError
    except Exception:  # pragma: no cover - botocore is a hard dep, defensive only
        return None


class RawAnnotationClient:
    """Thin, honest wrapper over the four S3 annotation operations."""

    def __init__(self, client: SupportsAnnotations):
        missing = [m for m in _REQUIRED_METHODS if not hasattr(client, m)]
        if missing:
            raise AnnotationAPIUnsupported(
                f"S3 client is missing {missing}. {_UPGRADE_HINT}"
            )
        self._client = client
        self._ClientError = _client_error_class()

    # -- helpers ---------------------------------------------------------

    def _code(self, exc: Exception) -> str:
        response = getattr(exc, "response", None) or {}
        return (response.get("Error") or {}).get("Code", "")

    def _reraise(self, exc: Exception, bucket: str, key: str, name: str):
        """Translate a botocore ClientError into a library error, or re-raise."""
        code = self._code(exc)
        loc = f"s3://{bucket}/{key} annotation {name!r}"
        if code == "NoSuchAnnotation":
            raise AnnotationNotFound(f"no annotation {loc}") from exc
        if code in ("TooManyAnnotations", "TooManyObjectAnnotations"):
            raise AnnotationLimitExceeded(
                f"object s3://{bucket}/{key} already holds the maximum of 1,000 "
                "annotations"
            ) from exc
        if code in ("EntityTooLarge", "AnnotationTooLarge", "MaxAnnotationSizeExceeded"):
            raise MemoryTooLarge(
                f"annotation payload for {loc} exceeds the 1 MB S3 limit"
            ) from exc
        raise

    # -- operations ------------------------------------------------------

    def put_annotation(self, bucket: str, key: str, name: str, payload: bytes) -> None:
        try:
            self._client.put_object_annotation(
                Bucket=bucket, Key=key, AnnotationName=name, AnnotationPayload=payload
            )
        except Exception as exc:  # noqa: BLE001 - narrowed in _reraise
            if self._ClientError and isinstance(exc, self._ClientError):
                self._reraise(exc, bucket, key, name)
            raise

    def get_annotation(self, bucket: str, key: str, name: str) -> bytes:
        try:
            resp = self._client.get_object_annotation(
                Bucket=bucket, Key=key, AnnotationName=name
            )
        except Exception as exc:  # noqa: BLE001
            if self._ClientError and isinstance(exc, self._ClientError):
                self._reraise(exc, bucket, key, name)
            raise
        payload = resp["AnnotationPayload"]
        # Real boto3 returns a StreamingBody; the fake returns a file-like or bytes.
        if hasattr(payload, "read"):
            return payload.read()
        return bytes(payload)

    def list_annotation_names(self, bucket: str, key: str, prefix: str = "") -> list[str]:
        """Return every annotation name on the object, following continuation."""
        names: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
            if prefix:
                kwargs["AnnotationPrefix"] = prefix
            if token:
                kwargs["ContinuationToken"] = token
            try:
                resp = self._client.list_object_annotations(**kwargs)
            except Exception as exc:  # noqa: BLE001
                if self._ClientError and isinstance(exc, self._ClientError):
                    self._reraise(exc, bucket, key, "")
                raise
            for ann in resp.get("Annotations", []) or []:
                names.append(ann["AnnotationName"])
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return names

    def delete_annotation(self, bucket: str, key: str, name: str) -> None:
        """Delete an annotation. S3's DeleteObjectAnnotation is idempotent, so a
        missing name is NOT an error here — existence checks live in the client."""
        try:
            self._client.delete_object_annotation(
                Bucket=bucket, Key=key, AnnotationName=name
            )
        except Exception as exc:  # noqa: BLE001
            if self._ClientError and isinstance(exc, self._ClientError):
                self._reraise(exc, bucket, key, name)
            raise
