from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from app.storage.base import ObjectInfo, ObjectNotFound, StorageError


class LocalStorage:
    """Almacenamiento en disco para desarrollo y pruebas."""

    name = "local"

    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        self.bucket = str(self._root)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root != path and self._root not in path.parents:
            raise StorageError("Clave fuera de la raíz de almacenamiento")
        return path

    def _info(self, path: Path) -> ObjectInfo:
        st = path.stat()
        return ObjectInfo(
            path.relative_to(self._root).as_posix(), st.st_size, None, datetime.fromtimestamp(st.st_mtime, UTC)
        )

    def list_objects(self, prefix: str = "", page_size: int = 1000) -> Iterator[ObjectInfo]:
        if not self._root.exists():
            return
        for path in sorted(self._root.rglob("*")):
            if path.is_file() and path.relative_to(self._root).as_posix().startswith(prefix):
                yield self._info(path)

    def head(self, key: str) -> ObjectInfo:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return self._info(path)

    def get(self, key: str, max_bytes: int) -> bytes:
        info = self.head(key)
        if info.size > max_bytes:
            raise StorageError(f"Objeto demasiado grande ({info.size} bytes)")
        return self._path(key).read_bytes()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
