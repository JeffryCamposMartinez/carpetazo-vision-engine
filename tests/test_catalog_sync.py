# ruff: noqa: E501
"""Sincronización del catálogo de Carpetazo (opción A). Se usa una API FALSA en memoria (httpx.MockTransport) con datos SINTÉTICOS: nunca hay red ni catálogo real."""

import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.catalog_mirror import cli as mirror_cli
from app.catalog_mirror.client import ALLOWED, CarpetazoCatalogClient, CatalogApiError, RequestBudgetExceeded
from app.catalog_mirror.mapping import InvalidRecord, card_record, classify_image, digest, relation_of, set_record
from app.catalog_mirror.sync import SyncOptions, mirror_stats, plan, run_sync
from app.database.mirror_models import MirrorCard, MirrorChange, MirrorGame, MirrorSet, MirrorSyncRun
from app.database.models import Base

R2 = "https://r2.ejemplo.invalid/Carpetazo.cl/Pokemon/Images"
PLACEHOLDER = "https://r2.ejemplo.invalid/Carpetazo.cl/Sin_Imagen/Carpetazo_TCG_Sin_Imagen.webp"


def api_card(pid, group, name=None, cat=3, number=None, image=None, extra=None):
    return {
        "productId": pid, "categoryId": cat, "groupId": group, "name": name or f"Carta de prueba {pid}", "hasImage": bool(image or True),
        "imageUrl": image or f"{R2}/{pid}.webp", "number": number or str(pid % 1000), "rarity": "Common",
        "extendedData": [{"name": "Number", "value": number or str(pid % 1000)}, *(extra or [])], "group": {"name": f"Set {group}"}, "game": "Pokémon",
    }  # fmt: skip


class FakeApi:
    """Imita los dos endpoints públicos de Carpetazo. Registra cada solicitud."""

    def __init__(self):
        self.sets = {3: [{"groupId": 10, "categoryId": 3, "name": "Set A", "abbreviation": "SA", "publishedOn": "2024-01-01", "modifiedOn": "2024-02-01"}, {"groupId": 11, "categoryId": 3, "name": "Set B", "abbreviation": None, "publishedOn": "2024-03-01", "modifiedOn": "2024-03-02"}],
                     100: [{"groupId": 10, "categoryId": 3, "name": "Set A", "abbreviation": "SA", "publishedOn": "2024-01-01", "modifiedOn": "2024-02-01"}, {"groupId": 9000001, "categoryId": 100, "name": "Solo español", "abbreviation": "SE", "publishedOn": None, "modifiedOn": None}]}  # fmt: skip
        self.cards = {
            (3, 10): [api_card(1001, 10), api_card(1002, 10)],
            (3, 11): [api_card(2001, 11)],
            (100, 10): [api_card(1001, 10, image=f"{R2}/es/1001.webp"), api_card(1002, 10, image=PLACEHOLDER)],  # categoryId 3: producto inglés visto en español
            (100, 9000001): [api_card(900001, 9000001, cat=100, extra=[{"name": "Language", "value": "es"}])],
        }  # fmt: skip
        self.log: list[str] = []
        self.fail: dict[str, list[int]] = {}  # ruta -> códigos de estado a devolver (en orden) antes de responder bien
        self.headers: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.log.append(path + ("?" + request.url.query.decode() if request.url.query else ""))
        key = path.removeprefix("/api")
        if self.fail.get(key):
            code = self.fail[key].pop(0)
            return httpx.Response(code, headers={"retry-after": "3"} if code == 429 else {}, json={"success": False})
        if request.method != "GET":
            return httpx.Response(405)
        if m := re.fullmatch(r"/api/tcg/catalog/sets", path):
            return httpx.Response(200, json={"success": True, "data": self.sets[int(request.url.params["categoryId"])]}, headers=self.headers)
        if m := re.fullmatch(r"/api/tcg/catalog/(\d+)/(\d+)/cards", path):
            return httpx.Response(200, json={"success": True, "data": self.cards.get((int(m[1]), int(m[2])), [])}, headers=self.headers)
        return httpx.Response(404)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make_client(api, clock=None, **kw):
    clock = clock or Clock()
    kw.setdefault("min_interval", 1.0)
    return CarpetazoCatalogClient("https://api.ejemplo.invalid/api", transport=httpx.MockTransport(api), sleep=clock.sleep, clock=clock.now, **kw), clock


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def dump(s):
    cards = {(c.category_id, c.product_id): (c.group_id, c.name, c.digest, c.relation, c.source_category_id, c.retired_at is None) for c in s.scalars(select(MirrorCard))}
    sets = {(x.category_id, x.group_id): (x.name, x.digest, x.retired_at is None) for x in s.scalars(select(MirrorSet))}
    return cards, sets


# ---- cliente
def test_client_only_allows_the_two_read_only_catalog_endpoints_and_never_fetches_images():
    api = FakeApi()
    client, _ = make_client(api)
    client.get_sets(3)
    client.get_cards(3, 10)
    for bad in ("/tcg/search", "/admin/catalog-import", "/images/1.webp", "/tcg/catalog/cards-data", "/tcg/catalog/3/10/cards/extra"):
        with pytest.raises(CatalogApiError, match="no permitida"):
            client._get(bad)
    assert all(p.startswith("/api/tcg/catalog") for p in api.log) and client.requests_made == 2 and len(ALLOWED) == 2
    with pytest.raises(ValueError, match="https"):
        CarpetazoCatalogClient("http://api.ejemplo.invalid/api")


def test_client_pauses_between_requests_and_honours_retry_after_and_backoff():
    api = FakeApi()
    client, clock = make_client(api, min_interval=2.0)
    client.get_sets(3)
    client.get_sets(3)
    assert clock.slept == [2.0]  # una sola pausa entre la primera y la segunda
    api.fail["/tcg/catalog/sets"] = [429, 503]
    client.get_sets(100)
    assert 3.0 in clock.slept  # Retry-After: 3
    assert client.retries == 2 and client.requests_made == 5


def test_client_gives_up_after_the_retry_limit_and_does_not_retry_client_errors():
    api = FakeApi()
    client, _ = make_client(api, max_retries=2)
    api.fail["/tcg/catalog/sets"] = [500, 500, 500, 500]
    with pytest.raises(CatalogApiError, match="500") as e:
        client.get_sets(3)
    assert e.value.retryable and client.requests_made == 3
    api.fail["/tcg/catalog/3/10/cards"] = [404]
    before = client.requests_made
    with pytest.raises(CatalogApiError, match="404") as e:
        client.get_cards(3, 10)
    assert not e.value.retryable and client.requests_made == before + 1


def test_client_validates_the_response_shape_and_does_not_follow_redirects():
    def handler(request):
        if "sets" in request.url.path:
            return httpx.Response(200, json={"success": False, "data": []})
        return httpx.Response(302, headers={"location": "https://otro.invalid/x"})

    client = CarpetazoCatalogClient("https://api.ejemplo.invalid/api", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(CatalogApiError, match="forma de respuesta"):
        client.get_sets(3)
    with pytest.raises(CatalogApiError, match="302"):
        client.get_cards(3, 1)


def test_client_respects_the_request_budget_and_low_rate_limit_headers():
    api = FakeApi()
    client, clock = make_client(api, max_requests=2)
    client.get_sets(3)
    client.get_sets(100)
    with pytest.raises(RequestBudgetExceeded):
        client.get_sets(3)
    api.headers = {"ratelimit-remaining": "4", "ratelimit-reset": "7"}
    client2, clock2 = make_client(api)
    client2.get_sets(3)
    assert 7.0 in clock2.slept  # casi sin cupo: espera a que se reinicie la ventana
    assert clock.slept  # (la pausa mínima también se aplicó en el primer cliente)


# ---- mapeo
def test_image_origin_is_classified_from_the_url_without_visiting_it():
    assert classify_image(None) == ("none", None)
    assert classify_image(PLACEHOLDER) == ("placeholder", None)
    assert classify_image("https://tcgplayer-cdn.tcgplayer.com/product/1_400w.jpg") == ("tcgplayer_cdn", None)
    assert classify_image("https://assets.tcgdex.net/es/x/y/high.webp") == ("tcgdex", None)
    assert classify_image(f"{R2}/Pok%C3%A9mon%20(es)/1.webp") == ("carpetazo_r2", "Carpetazo.cl/Pokemon/Images/Pokémon (es)/1.webp")
    assert classify_image("https://desconocido.invalid/a.png") == ("other", None)


def test_card_record_keeps_carpetazo_ids_and_never_invents_fields():
    r = card_record(100, api_card(900001, 9000001, cat=100, extra=[{"name": "Language", "value": "ES"}, {"name": "TcgplayerProductId", "value": "1234"}]))
    assert (r.category_id, r.product_id, r.group_id, r.source_category_id, r.language, r.tcgplayer_product_id) == (100, 900001, 9000001, 100, "es", 1234)
    plain = card_record(3, api_card(1001, 10))
    assert plain.language is None and plain.tcgplayer_product_id is None  # no se adivina el idioma ni el vínculo
    assert relation_of(3, 3) == "catalog_product" and relation_of(100, 3) == "spanish_view_of_english_product" and relation_of(100, 100) == "spanish_only" and relation_of(100, None) is None
    for bad in ({**api_card(1, 1), "productId": "abc"}, {**api_card(1, 1), "productId": 0}, {**api_card(1, 1), "groupId": None}, {**api_card(1, 1), "name": "  "}, {**api_card(1, 1), "productId": True}):
        with pytest.raises(InvalidRecord):
            card_record(3, bad)
    with pytest.raises(InvalidRecord):
        set_record(3, {"groupId": 1, "name": ""})


def test_digest_is_stable_order_independent_and_sensitive_to_content():
    a = card_record(3, api_card(1, 10, extra=[{"name": "Z", "value": "1"}, {"name": "A", "value": "2"}]))
    b = card_record(3, api_card(1, 10, extra=[{"name": "A", "value": "2"}, {"name": "Z", "value": "1"}]))
    assert a.digest == b.digest == card_record(3, api_card(1, 10, extra=[{"name": "Z", "value": "1"}, {"name": "A", "value": "2"}])).digest
    assert card_record(3, api_card(1, 10, name="Otro nombre", extra=[{"name": "Z", "value": "1"}, {"name": "A", "value": "2"}])).digest != a.digest
    assert digest({"a": 1}) == digest({"a": 1}) != digest({"a": 2})


# ---- sincronización
def test_first_sync_mirrors_everything_with_the_original_carpetazo_ids(session):
    api = FakeApi()
    client, _ = make_client(api)
    rep = run_sync(session, client)
    assert rep["status"] == "ok" and rep["requests_made"] == 2 + 4  # 2 listas de ediciones + 4 ediciones
    cards, sets = dump(session)
    assert set(cards) == {(3, 1001), (3, 1002), (3, 2001), (100, 1001), (100, 1002), (100, 900001)}
    assert set(sets) == {(3, 10), (3, 11), (100, 10), (100, 9000001)}
    assert cards[(3, 1001)][3] == "catalog_product" and cards[(100, 1001)][3] == "spanish_view_of_english_product" and cards[(100, 900001)][3] == "spanish_only"
    assert cards[(100, 1001)][4] == 3 and cards[(100, 900001)][4] == 100  # categoryId de origen tal como lo entrega la API
    assert {g.category_id: g.name for g in session.scalars(select(MirrorGame))} == {3: "Pokémon", 100: "Pokémon"}
    imgs = {(c.category_id, c.product_id): (c.image_origin, c.image_key) for c in session.scalars(select(MirrorCard))}
    assert imgs[(100, 1002)][0] == "placeholder" and imgs[(3, 1001)] == ("carpetazo_r2", "Carpetazo.cl/Pokemon/Images/1001.webp")
    assert not any("webp" in p or "jpg" in p for p in api.log)


def test_run_records_dates_duration_results_and_errors(session):
    client, _ = make_client(FakeApi())
    api_rep = run_sync(session, client)
    run = session.get(MirrorSyncRun, __import__("uuid").UUID(api_rep["run_id"]))
    assert run.status == "ok" and run.started_at and run.finished_at and run.duration_ms is not None and run.requests_made == 6
    assert run.counts["cards"]["inserted"] == 6 and run.counts["sets"]["inserted"] == 4 and run.scope["categories"] == [3, 100] and run.source_base_url.startswith("https://")


def test_sync_is_idempotent_and_does_not_rewrite_what_did_not_change(session):
    client, _ = make_client(FakeApi())
    run_sync(session, client)
    changes = len(session.scalars(select(MirrorChange)).all())
    changed_at = {(c.category_id, c.product_id): c.last_changed_at for c in session.scalars(select(MirrorCard))}
    before = dump(session)
    rep = run_sync(session, client, SyncOptions(force=True))
    assert rep["counts"]["cards"]["unchanged"] == 6 and rep["counts"]["cards"]["inserted"] == rep["counts"]["cards"]["updated"] == 0
    assert dump(session) == before and len(session.scalars(select(MirrorChange)).all()) == changes
    assert {(c.category_id, c.product_id): c.last_changed_at for c in session.scalars(select(MirrorCard))} == changed_at


def test_unchanged_sets_are_not_downloaded_again(session):
    api = FakeApi()
    client, _ = make_client(api)
    run_sync(session, client)
    api.log.clear()
    rep = run_sync(session, client)
    assert api.log == ["/api/tcg/catalog/sets?categoryId=3", "/api/tcg/catalog/sets?categoryId=100"]  # solo las listas: ninguna edición se repite
    assert rep["counts"]["sets"]["skipped_fresh"] == 4


def test_stale_sets_are_downloaded_again_after_max_age(session):
    api = FakeApi()
    client, _ = make_client(api)
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    run_sync(session, client, now_fn=lambda: t0)
    api.log.clear()
    run_sync(session, client, SyncOptions(max_age_days=7), now_fn=lambda: t0 + timedelta(days=8))
    assert len([p for p in api.log if p.endswith("/cards")]) == 4


def test_modified_cards_are_detected_with_before_and_after(session):
    api = FakeApi()
    client, _ = make_client(api)
    run_sync(session, client)
    api.cards[(3, 10)][0] = api_card(1001, 10, name="Nombre corregido")
    rep = run_sync(session, client, SyncOptions(force=True))
    assert rep["counts"]["cards"]["updated"] == 1 and rep["counts"]["cards"]["unchanged"] == 5
    ch = session.scalars(select(MirrorChange).where(MirrorChange.kind == "updated", MirrorChange.entity == "card")).one()
    assert (ch.category_id, ch.product_id) == (3, 1001) and ch.before["name"] == "Carta de prueba 1001" and ch.after["name"] == "Nombre corregido"
    assert session.get(MirrorCard, (3, 1001)).name == "Nombre corregido" and session.get(MirrorCard, (100, 1001)).name == "Carta de prueba 1001"


def test_set_modified_on_triggers_a_new_download_of_that_set_only(session):
    api = FakeApi()
    client, _ = make_client(api)
    run_sync(session, client)
    api.sets[3][1]["modifiedOn"] = "2024-06-01"
    api.cards[(3, 11)].append(api_card(2002, 11))
    api.log.clear()
    rep = run_sync(session, client)
    assert [p for p in api.log if p.endswith("/cards")] == ["/api/tcg/catalog/3/11/cards"]
    assert rep["counts"]["sets"]["updated"] == 1 and rep["counts"]["cards"]["inserted"] == 1 and session.get(MirrorCard, (3, 2002)) is not None


def test_cards_that_disappear_are_retired_not_deleted_and_can_reappear(session):
    api = FakeApi()
    client, _ = make_client(api)
    run_sync(session, client)
    gone = api.cards[(3, 10)].pop()
    rep = run_sync(session, client, SyncOptions(force=True))
    c = session.get(MirrorCard, (3, 1002))
    assert rep["counts"]["cards"]["retired"] == 1 and c is not None and c.retired_at is not None and c.name == "Carta de prueba 1002"
    api.cards[(3, 10)].append(gone)
    rep = run_sync(session, client, SyncOptions(force=True))
    assert rep["counts"]["cards"]["reappeared"] == 1 and session.get(MirrorCard, (3, 1002)).retired_at is None
    kinds = [x.kind for x in session.scalars(select(MirrorChange).where(MirrorChange.product_id == 1002, MirrorChange.category_id == 3).order_by(MirrorChange.changed_at))]
    assert set(kinds) >= {"inserted", "retired", "reappeared"}


def test_an_empty_listing_never_retires_existing_cards_and_sets_are_retired_only_from_a_valid_list(session):
    api = FakeApi()
    client, _ = make_client(api)
    run_sync(session, client)
    api.cards[(3, 11)] = []
    rep = run_sync(session, client, SyncOptions(force=True))
    assert session.get(MirrorCard, (3, 2001)).retired_at is None and rep["counts"]["cards"]["retired"] == 0 and any("vacía" in e.get("warning", "") for e in rep["errors"])
    api.sets[3] = [api.sets[3][0]]
    run_sync(session, client)
    assert session.get(MirrorSet, (3, 11)).retired_at is not None and session.get(MirrorCard, (3, 2001)) is not None
    api.sets[3] = []
    run_sync(session, client)
    assert session.get(MirrorSet, (3, 10)).retired_at is None  # una lista vacía no retira nada


def test_invalid_and_duplicate_rows_are_reported_and_skipped(session):
    api = FakeApi()
    api.cards[(3, 11)] = [api_card(2001, 11), {**api_card(2002, 11), "name": ""}, api_card(2001, 11, name="duplicado"), api_card(2003, 99)]
    client, _ = make_client(api)
    rep = run_sync(session, client)
    c = rep["counts"]["cards"]
    assert rep["status"] == "partial" and c["invalid"] == 2 and c["duplicates"] == 1
    assert session.get(MirrorCard, (3, 2001)).name == "Carta de prueba 2001" and session.get(MirrorCard, (3, 2002)) is None and session.get(MirrorCard, (3, 2003)) is None


def test_a_failing_set_does_not_stop_the_others_and_is_reported(session):
    api = FakeApi()
    api.fail["/tcg/catalog/3/11/cards"] = [500] * 10
    client, _ = make_client(api, max_retries=1)
    rep = run_sync(session, client)
    assert rep["status"] == "partial" and any(e["where"] == "3:11" for e in rep["errors"])
    assert session.get(MirrorCard, (3, 1001)) is not None and session.get(MirrorCard, (100, 900001)) is not None and session.get(MirrorCard, (3, 2001)) is None
    api.fail.clear()  # la API se recupera
    api.log.clear()
    again = run_sync(session, client, SyncOptions(resume=True))  # reanuda: solo lo que faltó
    assert again["status"] == "ok" and session.get(MirrorCard, (3, 2001)) is not None
    assert [p for p in api.log if p.endswith("/cards")] == ["/api/tcg/catalog/3/11/cards"]


def test_too_many_consecutive_errors_abort_the_run(session):
    api = FakeApi()
    for key in ("/tcg/catalog/3/10/cards", "/tcg/catalog/3/11/cards"):
        api.fail[key] = [500] * 20
    client, _ = make_client(api, max_retries=0)
    rep = run_sync(session, client, SyncOptions(max_consecutive_errors=2))
    assert rep["status"] == "failed" and client.requests_made < 6


def test_interrupted_sync_resumes_without_repeating_finished_sets_and_matches_a_full_run(session):
    api = FakeApi()
    client, _ = make_client(api, max_requests=4)  # 2 listas + solo 2 ediciones
    first = run_sync(session, client)
    assert first["status"] == "interrupted" and session.get(MirrorSyncRun, __import__("uuid").UUID(first["run_id"])).cursor["done"]
    done_before = set(session.get(MirrorSyncRun, __import__("uuid").UUID(first["run_id"])).cursor["done"])
    api.log.clear()
    client2, _ = make_client(api)
    second = run_sync(session, client2, SyncOptions(resume=True))
    fetched = [p for p in api.log if p.endswith("/cards")]
    assert second["status"] == "ok" and second["resumed_from"] == first["run_id"] and len(fetched) == 4 - len(done_before)
    assert not any(f"/{k.split(':')[0]}/{k.split(':')[1]}/cards" in " ".join(fetched) for k in done_before)
    ref_session = Session(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
    Base.metadata.create_all(ref_session.bind)
    run_sync(ref_session, make_client(FakeApi())[0])
    assert dump(session) == dump(ref_session)


def test_a_time_budget_also_interrupts_cleanly(session):
    client, _ = make_client(FakeApi())
    clock = iter(range(0, 10_000, 100))
    rep = run_sync(session, client, SyncOptions(max_minutes=1), clock=lambda: next(clock))
    assert rep["status"] == "interrupted" and any("minutos" in e["error"] for e in rep["errors"])


# ---- dry-run
def test_dry_run_estimates_requests_from_the_sets_lists_only_and_writes_nothing(session):
    api = FakeApi()
    client, _ = make_client(api)
    out = plan(session, client, SyncOptions())
    assert api.log == ["/api/tcg/catalog/sets?categoryId=3", "/api/tcg/catalog/sets?categoryId=100"]  # solo 2 peticiones, ninguna de cartas
    assert out["dry_run"] and out["estimated_requests"] == 2 + 4 and out["categories"][3]["sets_to_fetch_cards"] == 2 and out["categories"][100]["sets_new"] == 2
    assert session.scalars(select(MirrorSyncRun)).all() == [] and session.scalars(select(MirrorSet)).all() == [] and session.scalars(select(MirrorCard)).all() == []


def test_dry_run_after_a_sync_estimates_only_what_would_be_downloaded(session):
    api = FakeApi()
    client, _ = make_client(api)
    run_sync(session, client)
    api.sets[3][0]["modifiedOn"] = "2025-01-01"
    out = plan(session, make_client(api)[0], SyncOptions())
    assert out["estimated_requests"] == 2 + 1 and out["categories"][3]["reasons"].get("cambió modifiedOn") == 1 and out["categories"][3]["sets_skipped_unchanged"] == 1


def test_stats_summarise_the_mirror(session):
    run_sync(session, make_client(FakeApi())[0])
    s = mirror_stats(session)
    assert s["cards"] == 6 and s["sets"] == 4 and s["cards_retired"] == 0 and s["cards_by_category"] == {3: 3, 100: 3} and s["last_runs"][0]["status"] == "ok"


# ---- CLI y guardia
def test_cli_refuses_databases_that_are_not_the_private_lab(capsys):
    for url in ("postgresql+psycopg://u@127.0.0.1:5432/vision_dev", "postgresql+psycopg://u@db.ejemplo.com:5498/x"):
        assert mirror_cli.main(["sync", "--database-url", url]) == 2
        assert mirror_cli.main(["plan", "--database-url", url]) == 2
        assert mirror_cli.main(["status", "--database-url", url]) == 2
    with pytest.raises(SystemExit):
        mirror_cli.main(["sync"])  # sin valor por defecto de base de datos
    with pytest.raises(SystemExit):
        mirror_cli.main(["sync", "--database-url", "sqlite://", "--categories", "5"])  # alcance inicial: solo Pokémon 3 y 100


def test_cli_dry_run_uses_the_plan_and_does_not_write(tmp_path, monkeypatch, capsys):
    db = tmp_path / "lab.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
    api = FakeApi()
    monkeypatch.setattr(mirror_cli, "_client", lambda a: make_client(api)[0])
    assert mirror_cli.main(["sync", "--database-url", f"sqlite:///{db}", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] and out["estimated_requests"] == 6
    with Session(create_engine(f"sqlite:///{db}")) as s:
        assert s.scalars(select(MirrorSyncRun)).all() == []
    assert mirror_cli.main(["sync", "--database-url", f"sqlite:///{db}"]) == 0
    capsys.readouterr()
    assert mirror_cli.main(["status", "--database-url", f"sqlite:///{db}"]) == 0
    assert json.loads(capsys.readouterr().out)["cards"] == 6


# ---- PostgreSQL REAL del laboratorio (puerto 5498)
@pytest.mark.skipif(not __import__("os").environ.get("VISION_TEST_POSTGRES_URL"), reason="define VISION_TEST_POSTGRES_URL (laboratorio 127.0.0.1:5498, base *_test)")
def test_sync_runs_on_the_real_lab_postgres_and_is_idempotent_there_too():
    from sqlalchemy import text

    from app.database.guard import assert_lab_database
    from tests.test_postgres import URL, migrate

    assert_lab_database(URL)
    eng = create_engine(URL)
    assert (eng.url.database or "").endswith("_test")
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    migrate(eng, "upgrade", "head")
    api = FakeApi()
    with Session(eng) as s:
        t0 = datetime(2026, 11, 1, tzinfo=UTC)
        assert run_sync(s, make_client(api)[0], now_fn=lambda: t0)["status"] == "ok"
        before = dump(s)
        api.log.clear()
        rep = run_sync(s, make_client(api)[0], now_fn=lambda: t0 + timedelta(days=1))
        assert rep["counts"]["sets"]["skipped_fresh"] == 4 and dump(s) == before and len(api.log) == 2
        api.cards[(3, 10)].pop()
        rep = run_sync(s, make_client(api)[0], SyncOptions(force=True), now_fn=lambda: t0 + timedelta(days=2))
        assert rep["counts"]["cards"]["retired"] == 1 and s.get(MirrorCard, (3, 1002)).retired_at is not None
        assert plan(s, make_client(api)[0], SyncOptions(), now=t0 + timedelta(days=3))["estimated_requests"] == 2
    eng.dispose()


def test_cli_output_survives_a_windows_cp1252_console(tmp_path, monkeypatch):
    import io

    db = tmp_path / "w.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{db}"))
    monkeypatch.setattr(mirror_cli, "_client", lambda a: make_client(FakeApi())[0])
    raw = io.BytesIO()
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(raw, encoding="cp1252", write_through=True))
    assert mirror_cli.main(["sync", "--dry-run", "--database-url", f"sqlite:///{db}"]) == 0  # el aviso del limitador lleva «≈»
    assert "estimated_requests" in raw.getvalue().decode("utf-8")
