"""S3-compatible object storage (MinIO locally). Holds the heavy payloads referenced by claim checks."""

from __future__ import annotations

import json
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings


class StorageError(Exception):
    """Raised when the object store is unreachable or misbehaves (transient)."""


class ObjectMissingError(StorageError):
    """The object does not exist (deleted or never written). Retrying cannot help."""


def _is_missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


def _drop_expect_on_empty_body(request, **_):
    # An empty PUT sent with "Expect: 100-continue" makes MinIO answer twice (100 + 200); the stray 200
    # stays in the keep-alive socket and desynchronises the *next* request (it hangs until read timeout).
    if request.headers.get("Content-Length") in ("0", b"0") and "Expect" in request.headers:
        del request.headers["Expect"]


@lru_cache
def client(fast: bool = False):
    """S3 client. `fast=True` is for cheap metadata calls on the request path: fail in seconds, not minutes,
    when storage is unreachable (a half-open keep-alive socket would otherwise wait for the full read timeout)."""
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 1 if fast else 2, "mode": "standard"},
            connect_timeout=2 if fast else 3,
            read_timeout=3 if fast else 15,
        ),
    )
    s3.meta.events.register("before-send.s3.*", _drop_expect_on_empty_body)
    return s3


def ensure_buckets() -> None:
    for bucket in (settings.s3_bucket_documents, settings.s3_bucket_results):
        try:
            client().head_bucket(Bucket=bucket)
        except ClientError:
            client().create_bucket(Bucket=bucket)


def document_key(sha256: str, extension: str) -> str:
    # Content-addressed: the same file uploaded twice is stored once.
    return f"sha256/{sha256[:2]}/{sha256}{extension}"


def exists(bucket: str, key: str) -> bool:
    try:
        client(fast=True).head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if _is_missing(exc):
            return False
        raise StorageError(str(exc)) from exc
    except BotoCoreError as exc:
        raise StorageError(str(exc)) from exc


def upload_file(path: str, bucket: str, key: str, content_type: str, metadata: dict | None = None) -> None:
    try:
        client().upload_file(
            path, bucket, key, ExtraArgs={"ContentType": content_type, "Metadata": metadata or {}}
        )
    except (BotoCoreError, ClientError) as exc:
        raise StorageError(str(exc)) from exc


def download_file(bucket: str, key: str, path: str) -> None:
    try:
        client().download_file(bucket, key, path)
    except ClientError as exc:
        if _is_missing(exc):
            raise ObjectMissingError(f"{bucket}/{key} does not exist") from exc
        raise StorageError(str(exc)) from exc
    except BotoCoreError as exc:
        raise StorageError(str(exc)) from exc


def put_bytes(bucket: str, key: str, data: bytes, content_type: str) -> None:
    try:
        client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    except (BotoCoreError, ClientError) as exc:
        raise StorageError(str(exc)) from exc


def put_json(bucket: str, key: str, payload: dict) -> None:
    put_bytes(bucket, key, json.dumps(payload, ensure_ascii=False, default=str).encode(), "application/json")


def get_object_stream(bucket: str, key: str):
    try:
        obj = client().get_object(Bucket=bucket, Key=key)
        return obj["Body"], obj.get("ContentLength"), obj.get("ContentType")
    except ClientError as exc:
        if _is_missing(exc):
            raise ObjectMissingError(f"{bucket}/{key} does not exist") from exc
        raise StorageError(str(exc)) from exc
    except BotoCoreError as exc:
        raise StorageError(str(exc)) from exc


def get_bytes(bucket: str, key: str) -> bytes:
    body, _, _ = get_object_stream(bucket, key)
    return body.read()


def ping() -> bool:
    try:
        client(fast=True).head_bucket(Bucket=settings.s3_bucket_documents)
        return True
    except Exception:
        return False
