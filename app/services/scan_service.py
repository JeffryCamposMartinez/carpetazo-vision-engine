"""Estado en memoria del escaneo: resultados, idempotencia, rate limit y concurrencia.

Es estado de un solo proceso (un worker de uvicorn). Si el servicio escala a varios
procesos habrá que moverlo a Redis.
"""

import asyncio
import threading
import time
from collections import OrderedDict, deque

from app.core.config import Settings
from app.schemas.scan import ScanResponse


class ScanService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._results: OrderedDict[str, tuple[float, ScanResponse]] = OrderedDict()
        self._idempotency: dict[tuple[str, str], str] = {}
        self._hits: dict[str, deque[float]] = {}
        self._active: set[str] = set()
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_scans)

    def _evict(self, now: float) -> None:
        ttl = self._settings.scan_result_ttl_seconds
        while self._results:
            scan_id, (created, _) = next(iter(self._results.items()))
            if now - created <= ttl and len(self._results) <= self._settings.scan_store_max_entries:
                break
            del self._results[scan_id]
        live = {r.scan_id for _, r in self._results.values()}
        self._idempotency = {k: v for k, v in self._idempotency.items() if v in live}

    def allow_request(self, session_id: str) -> int | None:
        """Devuelve None si se permite, o los segundos de espera si se excedió el límite."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(session_id, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self._settings.scan_rate_limit_per_minute:
                return max(1, int(60 - (now - hits[0])) + 1)
            hits.append(now)
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v}
            return None

    def try_begin(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._active:
                return False
            self._active.add(session_id)
            return True

    def end(self, session_id: str) -> None:
        with self._lock:
            self._active.discard(session_id)

    def find_idempotent(self, session_id: str, key: str) -> ScanResponse | None:
        with self._lock:
            self._evict(time.monotonic())
            scan_id = self._idempotency.get((session_id, key))
            entry = self._results.get(scan_id) if scan_id else None
            return entry[1] if entry else None

    def save(self, result: ScanResponse, idempotency_key: str | None) -> None:
        with self._lock:
            now = time.monotonic()
            self._results[result.scan_id] = (now, result)
            if idempotency_key:
                self._idempotency[(result.session_id, idempotency_key)] = result.scan_id
            self._evict(now)

    def get(self, scan_id: str) -> ScanResponse | None:
        with self._lock:
            self._evict(time.monotonic())
            entry = self._results.get(scan_id)
            return entry[1] if entry else None
