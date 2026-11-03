from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None


class ObjectNotFound(Exception):
    pass


class StorageError(Exception):
    """Error de almacenamiento sin detalles sensibles (nunca incluye credenciales)."""


class ObjectStorage(Protocol):
    name: str  # identificador del backend ("local" | "s3")
    bucket: str  # bucket o raíz lógica

    def list_objects(self, prefix: str = "", page_size: int = 1000) -> Iterator[ObjectInfo]: ...

    def head(self, key: str) -> ObjectInfo: ...

    def get(self, key: str, max_bytes: int) -> bytes: ...

    def put(self, key: str, data: bytes, content_type: str) -> None: ...
