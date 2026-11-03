"""Cliente mínimo de TCGdex (solo lectura). Nunca se invoca al arrancar la API."""

import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.core.config import Settings


class ProviderError(Exception):
    pass


class TcgdexClient:
    name = "tcgdex"

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._s = settings
        self._sleep = sleep
        self._last = 0.0
        ua = f"CarpetazoVisionEngine/{settings.engine_version} ({settings.provider_contact or 'sin contacto'})"
        self._http = httpx.Client(
            base_url=settings.tcgdex_base_url,
            timeout=httpx.Timeout(settings.provider_timeout_seconds, connect=5.0),
            headers={"User-Agent": ua},
            transport=transport,
        )

    def _get(self, path: str):
        for attempt in range(self._s.provider_max_retries + 1):
            wait = self._s.provider_min_interval_seconds - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                r = self._http.get(path)
            except httpx.TransportError as exc:
                error = f"red: {type(exc).__name__}"
            else:
                if r.status_code == 404:
                    raise ProviderError(f"404 en {path}")
                if r.status_code < 400:
                    try:
                        return r.json()
                    except ValueError:
                        raise ProviderError(f"respuesta no JSON en {path}") from None
                if r.status_code not in {429, 500, 502, 503, 504}:
                    raise ProviderError(f"HTTP {r.status_code} en {path}")
                error = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    self._sleep(min(int(retry_after), 30))
            if attempt == self._s.provider_max_retries:
                raise ProviderError(f"{error} en {path} tras {attempt + 1} intentos")
            self._sleep(min(2**attempt, 10))
        raise AssertionError("inalcanzable")

    @property
    def base_url(self) -> str:
        return self._s.tcgdex_base_url

    game_slug = "pokemon"
    game_name = "Pokémon TCG"

    def list_sets(self, language: str) -> list[dict]:
        data = self._get(f"/{language}/sets")
        if not isinstance(data, list):
            raise ProviderError(f"respuesta inesperada en /{language}/sets")
        return data

    def get_set(self, set_id: str, language: str) -> dict:
        data = self._get(f"/{language}/sets/{set_id}")
        if not isinstance(data, dict):
            raise ProviderError(f"respuesta inesperada para el set {set_id}")
        return data

    def get_card(self, card_id: str, language: str) -> dict:
        data = self._get(f"/{language}/cards/{card_id}")
        if not isinstance(data, dict):
            raise ProviderError(f"respuesta inesperada para la carta {card_id}")
        return data

    def card_variants(self, card_id: str, language: str = "en") -> dict:
        """Solo las variantes de impresión de una carta (sin precios)."""
        d = self._get(f"/{language}/cards/{card_id}")
        keep = ("type", "subtype", "foil", "stamp", "size", "variantId")
        return {
            "id": d["id"],
            "name": d.get("name"),
            "variants": d.get("variants") or {},
            "variants_detailed": [{k: v[k] for k in keep if k in v} for v in d.get("variants_detailed") or []],
        }

    def snapshot(self, language: str, progress: Callable[[str], None] = lambda _: None) -> dict:
        """Sets y cartas (id, número local, nombre) de un idioma. Una petición por set."""
        sets = []
        listing = self._get(f"/{language}/sets")
        for i, s in enumerate(listing, 1):
            progress(f"[{i}/{len(listing)}] {s['id']}")
            d = self._get(f"/{language}/sets/{s['id']}")
            sets.append(
                {
                    "id": d["id"],
                    "name": d["name"],
                    "code": (d.get("abbreviation") or {}).get("official") or d.get("tcgOnline"),
                    "release_date": d.get("releaseDate"),
                    "series": (d.get("serie") or {}).get("name"),
                    "card_count": d.get("cardCount") or {},
                    "cards": [
                        {"id": c["id"], "local_id": c["localId"], "name": c.get("name", "")} for c in d.get("cards", [])
                    ],
                }
            )
        return {
            "provider": self.name,
            "language": language,
            "fetched_at": datetime.now(UTC).isoformat(),
            "sets": sets,
        }
