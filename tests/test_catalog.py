import uuid

import pytest

from app.database.models import Card, CardPrinting, CardSet, Game
from tests.conftest import SERVICE_HEADERS


@pytest.fixture
def seeded(db_factory):
    with db_factory() as db:
        game = Game(id=uuid.uuid4(), name="Pokemon TCG", slug="pokemon")
        s_en = CardSet(game_id=game.id, external_id="base1", name="Base Set", code="BS", language="en")
        s_es = CardSet(game_id=game.id, external_id="base1", name="Set Base", code="BS", language="es")
        card = Card(game_id=game.id, external_id="pikachu-base", canonical_name="Pikachu")
        db.add_all([game, s_en, s_es, card])
        db.flush()
        p_en = CardPrinting(
            card_id=card.id,
            set_id=s_en.id,
            external_id="base1-58-en",
            language="en",
            collector_number="58",
            variant="normal",
        )
        p_es = CardPrinting(
            card_id=card.id,
            set_id=s_es.id,
            external_id="base1-58-es",
            language="es",
            collector_number="58",
            variant="holo",
            image_reference="img/x.webp",
        )
        db.add_all([p_en, p_es])
        db.commit()
        return {"card": str(card.id), "p_es": str(p_es.id)}


def test_catalog_requires_auth(client):
    assert client.get("/v1/catalog/sets").status_code == 401


def test_search_and_language_filter(client, seeded):
    r = client.get("/v1/catalog/search", params={"q": "pika"}, headers=SERVICE_HEADERS)
    assert len(r.json()) == 2
    r = client.get("/v1/catalog/search", params={"q": "pika", "language": "es"}, headers=SERVICE_HEADERS)
    assert [p["variant"] for p in r.json()] == ["holo"]
    assert r.json()[0]["has_image"] is True


def test_search_treats_wildcards_literally(client, seeded):
    assert client.get("/v1/catalog/search", params={"q": "%%"}, headers=SERVICE_HEADERS).json() == []


def test_sets_card_printing(client, seeded, session_headers):
    assert len(client.get("/v1/catalog/sets", headers=session_headers).json()) == 2
    card = client.get(f"/v1/catalog/cards/{seeded['card']}", headers=SERVICE_HEADERS).json()
    assert len(card["printings"]) == 2
    p = client.get(f"/v1/catalog/printings/{seeded['p_es']}", headers=SERVICE_HEADERS).json()
    assert p["set_name"] == "Set Base"
    assert client.get(f"/v1/catalog/cards/{uuid.uuid4()}", headers=SERVICE_HEADERS).status_code == 404
