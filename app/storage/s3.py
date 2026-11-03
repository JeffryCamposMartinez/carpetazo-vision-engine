"""Almacenamiento S3-compatible (Cloudflare R2). Las credenciales vienen solo del entorno del servidor."""

from collections.abc import Iterator
from datetime import UTC, datetime

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.storage.base import ObjectInfo, ObjectNotFound, StorageError


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str, client):
        self.bucket = bucket
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3Storage":
        endpoint = settings.resolved_s3_endpoint
        if not (endpoint and settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key):
            raise StorageError(
                "Faltan variables: VISION_R2_ACCOUNT_ID (o VISION_S3_ENDPOINT_URL), VISION_S3_BUCKET, "
                "VISION_S3_ACCESS_KEY_ID y VISION_S3_SECRET_ACCESS_KEY"
            )
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=30,
            ),
        )
        return cls(settings.s3_bucket, SkewTolerantClient(client))

    def list_objects(self, prefix: str = "", page_size: int = 1000) -> Iterator[ObjectInfo]:
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": page_size}
            if token:
                kwargs["ContinuationToken"] = token
            try:
                page = self._client.list_objects_v2(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                raise StorageError(f"No se pudo listar el bucket ({_code(exc)})") from None
            for obj in page.get("Contents", []):
                yield ObjectInfo(
                    obj["Key"], obj["Size"], (obj.get("ETag") or "").strip('"') or None, obj.get("LastModified")
                )
            if not page.get("IsTruncated"):
                return
            token = page["NextContinuationToken"]

    def head(self, key: str) -> ObjectInfo:
        try:
            r = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFound(key) from None
            raise StorageError(f"head falló ({_code(exc)})") from None
        except BotoCoreError as exc:
            raise StorageError(f"head falló ({_code(exc)})") from None
        return ObjectInfo(key, r["ContentLength"], (r.get("ETag") or "").strip('"') or None, r.get("LastModified"))

    def get(self, key: str, max_bytes: int) -> bytes:
        info = self.head(key)
        if info.size > max_bytes:
            raise StorageError(f"Objeto demasiado grande ({info.size} bytes)")
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read(max_bytes + 1)
        except ClientError as exc:
            if _code(exc) in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectNotFound(key) from None
            raise StorageError(f"get falló ({_code(exc)})") from None
        except BotoCoreError as exc:
            raise StorageError(f"get falló ({_code(exc)})") from None

    def put(self, key: str, data: bytes, content_type: str) -> None:
        # Sin ACL: el bucket nunca se hace público desde aquí.
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"put falló ({_code(exc)})") from None


class SkewTolerantClient:
    """Si el reloj local difiere del de R2 (RequestTimeTooSkewed), corrige la hora de firma y reintenta una vez.

    Solo cambia la hora usada para firmar las peticiones de este proceso; no toca el reloj del sistema.
    """

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        method = getattr(self._client, name)
        if not callable(method):
            return method

        def call(**kwargs):
            try:
                return method(**kwargs)
            except ClientError as exc:
                if _code(exc) != "RequestTimeTooSkewed":
                    raise
                headers = exc.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                if not _apply_clock_offset(headers.get("date")):
                    raise
                return method(**kwargs)

        return call


def _apply_clock_offset(http_date: str | None) -> bool:
    if not http_date:
        return False
    from email.utils import parsedate_to_datetime

    import botocore.auth as botocore_auth

    offset = parsedate_to_datetime(http_date) - datetime.now(UTC)
    original = botocore_auth.get_current_datetime

    def corrected(*args, **kwargs):
        return original(*args, **kwargs) + offset

    botocore_auth.get_current_datetime = corrected
    return True


def _code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "ClientError"))
    return type(exc).__name__
