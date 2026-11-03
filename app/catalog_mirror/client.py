# ruff: noqa: E501
"""Cliente de SOLO LECTURA de la API pública de Carpetazo.cl (opción A).

- Únicos endpoints permitidos (lista blanca): `GET /tcg/catalog/sets?categoryId=N` y `GET /tcg/catalog/{categoryId}/{groupId}/cards`.
  Cualquier otra ruta se rechaza antes de enviar nada: no hay descargas de imágenes (ni de R2, ni de ningún otro host), no se siguen redirecciones y no hay escrituras.
- Respeta el limitador de la API (por defecto 2000 solicitudes / 15 min por IP): pausa mínima entre peticiones (1 s por defecto ≈ 900 / 15 min),
  usa las cabeceras `RateLimit-*` si las hay, obedece `Retry-After` en 429 y reintenta con espera creciente los errores transitorios.
- Presupuesto opcional de solicitudes (`max_requests`) para cortar a tiempo y poder reanudar.
- Sin credenciales: los endpoints son públicos. Ninguna clave va en el código.
"""

import re
import time
from collections.abc import Callable

import httpx

DEFAULT_BASE_URL = "https://api.carpetazo.cl/api"  # el que usa el frontend de Carpetazo
USER_AGENT = "CarpetazoVisionEngine-catalog-mirror/0.1 (sincronizacion de solo lectura)"
ALLOWED = (re.compile(r"^/tcg/catalog/sets$"), re.compile(r"^/tcg/catalog/\d{1,9}/\d{1,9}/cards$"))
MAX_RETRY_AFTER = 120.0


class CatalogApiError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status, self.retryable = status, retryable


class RequestBudgetExceeded(Exception):
    pass


class CarpetazoCatalogClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 2.0,
        max_requests: int | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")) and transport is None:
            raise ValueError("la URL base debe ser https (o un servidor local de pruebas)")
        self.base_url = base_url.rstrip("/")
        self.min_interval, self.max_retries, self.backoff, self.max_requests = min_interval, max_retries, backoff, max_requests
        self._sleep, self._clock = sleep, clock
        self._http = httpx.Client(timeout=timeout, follow_redirects=False, transport=transport, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._last = None
        self.requests_made = 0
        self.retries = 0
        self.throttled_seconds = 0.0

    def close(self) -> None:
        self._http.close()

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self.throttled_seconds += wait
                self._sleep(wait)

    def _get(self, path: str, params: dict | None = None) -> list[dict]:
        if not any(p.match(path) for p in ALLOWED):
            raise CatalogApiError(f"ruta no permitida por el cliente (solo lectura del catálogo): {path}")
        attempt = 0
        while True:
            if self.max_requests is not None and self.requests_made >= self.max_requests:
                raise RequestBudgetExceeded(f"se alcanzó el presupuesto de {self.max_requests} solicitudes")
            self._throttle()
            self._last = self._clock()
            self.requests_made += 1
            try:
                r = self._http.get(self.base_url + path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                err = CatalogApiError(f"error de red: {type(exc).__name__}", retryable=True)
            else:
                remaining, reset = r.headers.get("ratelimit-remaining"), r.headers.get("ratelimit-reset")
                if remaining is not None and remaining.isdigit() and int(remaining) <= 10 and reset and reset.replace(".", "", 1).isdigit():
                    wait = min(float(reset), MAX_RETRY_AFTER)
                    self.throttled_seconds += wait
                    self._sleep(wait)  # casi sin cupo: se espera a que se reinicie la ventana
                if r.status_code == 200:
                    return self._parse(r)
                if r.status_code == 429:
                    ra = r.headers.get("retry-after", "")
                    wait = min(float(ra), MAX_RETRY_AFTER) if ra.replace(".", "", 1).isdigit() else self.backoff * (2**attempt)
                    err = CatalogApiError("429: el limitador de la API pidió esperar", 429, True)
                    self.throttled_seconds += wait
                    self._sleep(wait)
                elif r.status_code >= 500:
                    err = CatalogApiError(f"{r.status_code}: error del servidor de Carpetazo", r.status_code, True)
                else:
                    raise CatalogApiError(f"{r.status_code}: respuesta inesperada", r.status_code, False)
            attempt += 1
            self.retries += 1
            if not err.retryable or attempt > self.max_retries:
                raise err
            self._sleep(self.backoff * (2 ** (attempt - 1)))

    @staticmethod
    def _parse(r: httpx.Response) -> list[dict]:
        try:
            body = r.json()
        except ValueError:
            raise CatalogApiError("la respuesta no es JSON", r.status_code) from None
        if not isinstance(body, dict) or body.get("success") is not True or not isinstance(body.get("data"), list):
            raise CatalogApiError("forma de respuesta inesperada (se esperaba {success: true, data: [...]})", r.status_code)
        return [row for row in body["data"] if isinstance(row, dict)]

    def get_sets(self, category_id: int) -> list[dict]:
        return self._get("/tcg/catalog/sets", {"categoryId": int(category_id)})

    def get_cards(self, category_id: int, group_id: int) -> list[dict]:
        return self._get(f"/tcg/catalog/{int(category_id)}/{int(group_id)}/cards")
