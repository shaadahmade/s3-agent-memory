"""In-memory fake of the S3 annotation surface — a DELIVERABLE, not a mock.

This is a drop-in for the boto3 S3 client's annotation subset (plus the plain
object operations needed to model durability). It faithfully reproduces the
semantics the library depends on, so other people can test their own code
against it:

  * annotations are a per-(bucket, key) map of name -> bytes
  * ≤ 1 MB per annotation payload            -> ClientError 'EntityTooLarge'
  * ≤ 1,000 distinct annotation names/object -> ClientError 'TooManyAnnotations'
  * writing an existing name is last-writer-wins (no error)
  * GetObjectAnnotation on a missing name    -> ClientError 'NoSuchAnnotation'
  * DeleteObjectAnnotation is idempotent (no error on a missing name), matching
    the real API which does not model NoSuchAnnotation for delete
  * copy_object copies the object body AND all of its annotations
  * delete_object destroys the object and its annotations, nothing else

Errors are raised as real ``botocore.exceptions.ClientError`` so the library's
``raw.py`` handles the fake and real AWS through identical code paths.
"""

from __future__ import annotations

import io
from typing import Optional

from botocore.exceptions import ClientError

MAX_ANNOTATION_BYTES = 1_000_000
MAX_ANNOTATIONS_PER_OBJECT = 1_000


def _client_error(code: str, message: str, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}}, operation
    )


class _Object:
    def __init__(self, body: bytes = b""):
        self.body = body
        self.annotations: dict[str, bytes] = {}


class FakeS3:
    """A minimal, faithful in-memory S3 with object annotations."""

    def __init__(self) -> None:
        # (bucket, key) -> _Object
        self._objects: dict[tuple[str, str], _Object] = {}
        # Test hook: names to vanish on the NEXT get (models a list/get race).
        self._vanish_on_get: set[tuple[str, str, str]] = set()

    # -- plain object operations ----------------------------------------

    def put_object(self, Bucket: str, Key: str, Body: bytes = b"") -> dict:
        self._objects[(Bucket, Key)] = _Object(Body)
        return {"ETag": '"fake"'}

    def _require_object(self, bucket: str, key: str, operation: str) -> _Object:
        obj = self._objects.get((bucket, key))
        if obj is None:
            raise _client_error(
                "NoSuchKey", f"object s3://{bucket}/{key} does not exist", operation
            )
        return obj

    def copy_object(self, Bucket: str, Key: str, CopySource: dict | str) -> dict:
        if isinstance(CopySource, str):
            src_bucket, _, src_key = CopySource.partition("/")
        else:
            src_bucket = CopySource["Bucket"]
            src_key = CopySource["Key"]
        src = self._require_object(src_bucket, src_key, "CopyObject")
        # Annotations travel with the object on copy — this IS the product thesis.
        dst = _Object(src.body)
        dst.annotations = dict(src.annotations)
        self._objects[(Bucket, Key)] = dst
        return {"CopyObjectResult": {"ETag": '"fake"'}}

    def delete_object(self, Bucket: str, Key: str) -> dict:
        # Destroys the object and, with it, every annotation. Nothing else.
        self._objects.pop((Bucket, Key), None)
        return {}

    # -- annotation operations ------------------------------------------

    def put_object_annotation(
        self,
        Bucket: str,
        Key: str,
        AnnotationName: str,
        AnnotationPayload: bytes,
        **_: object,
    ) -> dict:
        obj = self._require_object(Bucket, Key, "PutObjectAnnotation")
        payload = (
            AnnotationPayload
            if isinstance(AnnotationPayload, (bytes, bytearray))
            else bytes(AnnotationPayload)
        )
        if len(payload) > MAX_ANNOTATION_BYTES:
            raise _client_error(
                "EntityTooLarge",
                f"annotation payload {len(payload)} bytes exceeds 1 MB limit",
                "PutObjectAnnotation",
            )
        is_new = AnnotationName not in obj.annotations
        if is_new and len(obj.annotations) >= MAX_ANNOTATIONS_PER_OBJECT:
            raise _client_error(
                "TooManyAnnotations",
                f"object already has {MAX_ANNOTATIONS_PER_OBJECT} annotations",
                "PutObjectAnnotation",
            )
        obj.annotations[AnnotationName] = bytes(payload)  # last-writer-wins
        return {"AnnotationName": AnnotationName, "ETag": '"fake"'}

    def get_object_annotation(
        self, Bucket: str, Key: str, AnnotationName: str, **_: object
    ) -> dict:
        obj = self._require_object(Bucket, Key, "GetObjectAnnotation")
        vanish = (Bucket, Key, AnnotationName)
        if vanish in self._vanish_on_get:
            self._vanish_on_get.discard(vanish)
            obj.annotations.pop(AnnotationName, None)
        if AnnotationName not in obj.annotations:
            raise _client_error(
                "NoSuchAnnotation",
                f"annotation {AnnotationName!r} not found",
                "GetObjectAnnotation",
            )
        return {"AnnotationPayload": io.BytesIO(obj.annotations[AnnotationName])}

    def list_object_annotations(
        self,
        Bucket: str,
        Key: str,
        AnnotationPrefix: Optional[str] = None,
        ContinuationToken: Optional[str] = None,
        MaxAnnotationResults: Optional[int] = None,
        **_: object,
    ) -> dict:
        obj = self._require_object(Bucket, Key, "ListObjectAnnotations")
        names = sorted(obj.annotations)
        if AnnotationPrefix:
            names = [n for n in names if n.startswith(AnnotationPrefix)]
        annotations = [
            {
                "AnnotationName": n,
                "Size": len(obj.annotations[n]),
                "ETag": '"fake"',
            }
            for n in names
        ]
        return {
            "Annotations": annotations,
            "Bucket": Bucket,
            "Key": Key,
            "AnnotationCount": len(annotations),
        }

    def delete_object_annotation(
        self, Bucket: str, Key: str, AnnotationName: str, **_: object
    ) -> dict:
        obj = self._require_object(Bucket, Key, "DeleteObjectAnnotation")
        # Idempotent: no error if the name is absent (matches real S3).
        obj.annotations.pop(AnnotationName, None)
        return {}

    # -- test hooks ------------------------------------------------------

    def schedule_vanish_on_get(self, bucket: str, key: str, name: str) -> None:
        """Make ``name`` disappear the next time it is fetched (list/get race)."""
        self._vanish_on_get.add((bucket, key, name))

    def plant_raw_annotation(
        self, bucket: str, key: str, name: str, payload: bytes
    ) -> None:
        """Write raw bytes directly, bypassing the library (for C2's bad plant)."""
        obj = self._require_object(bucket, key, "PutObjectAnnotation")
        obj.annotations[name] = payload

    def annotation_names(self, bucket: str, key: str) -> list[str]:
        """Introspection helper for tests (not part of the S3 API)."""
        return sorted(self._objects[(bucket, key)].annotations)
