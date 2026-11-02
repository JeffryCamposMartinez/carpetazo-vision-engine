import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database.models import Card, CardPrinting, CardSet, Game
from app.schemas.catalog import CardOut, PrintingOut, SetOut


def _printing_out(p: CardPrinting) -> PrintingOut:
    return PrintingOut(
        id=str(p.id),
        card_id=str(p.card_id),
        card_name=p.card.canonical_name,
        set_id=str(p.set_id),
        set_name=p.set.name,
        set_code=p.set.code,
        collector_number=p.collector_number,
        language=p.language,
        rarity=p.rarity,
        variant=p.variant,
        has_image=bool(p.image_reference),
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_printings(
    db: Session, q: str, game: str, language: str | None, limit: int, offset: int
) -> list[PrintingOut]:
    stmt = (
        select(CardPrinting)
        .join(Card)
        .join(Game, Card.game_id == Game.id)
        .options(joinedload(CardPrinting.card), joinedload(CardPrinting.set))
        .where(Game.slug == game, Card.canonical_name.ilike(f"%{_escape_like(q)}%", escape="\\"))
        .order_by(Card.canonical_name, CardPrinting.collector_number)
        .limit(limit)
        .offset(offset)
    )
    if language:
        stmt = stmt.where(CardPrinting.language == language)
    return [_printing_out(p) for p in db.scalars(stmt).unique()]


def list_sets(db: Session, game: str, language: str | None) -> list[SetOut]:
    stmt = select(CardSet).join(Game).where(Game.slug == game).order_by(CardSet.release_date.desc(), CardSet.name)
    if language:
        stmt = stmt.where(CardSet.language == language)
    return [
        SetOut(id=str(s.id), name=s.name, code=s.code, language=s.language, release_date=s.release_date)
        for s in db.scalars(stmt)
    ]


def get_card(db: Session, card_id: uuid.UUID) -> CardOut | None:
    card = db.scalar(
        select(Card).where(Card.id == card_id).options(joinedload(Card.printings).joinedload(CardPrinting.set))
    )
    if card is None:
        return None
    for p in card.printings:
        p.card = card
    return CardOut(id=str(card.id), name=card.canonical_name, printings=[_printing_out(p) for p in card.printings])


def get_printing(db: Session, printing_id: uuid.UUID) -> PrintingOut | None:
    p = db.scalar(
        select(CardPrinting)
        .where(CardPrinting.id == printing_id)
        .options(joinedload(CardPrinting.card), joinedload(CardPrinting.set))
    )
    return _printing_out(p) if p else None
