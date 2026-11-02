import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.catalog.services import queries
from app.core.security import require_access
from app.database.session import get_db
from app.schemas.catalog import CardOut, PrintingOut, SetOut

router = APIRouter(prefix="/v1/catalog", dependencies=[Depends(require_access)])


def _not_found(what: str) -> HTTPException:
    return HTTPException(404, detail={"error": "not_found", "detail": f"{what} no encontrada"})


@router.get("/search", response_model=list[PrintingOut])
def search(
    q: str = Query(min_length=2, max_length=100),
    game: str = Query(default="pokemon", max_length=50),
    language: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[PrintingOut]:
    return queries.search_printings(db, q, game, language, limit, offset)


@router.get("/sets", response_model=list[SetOut])
def sets(
    game: str = Query(default="pokemon", max_length=50),
    language: str | None = Query(default=None, max_length=10),
    db: Session = Depends(get_db),
) -> list[SetOut]:
    return queries.list_sets(db, game, language)


@router.get("/cards/{card_id}", response_model=CardOut)
def card(card_id: uuid.UUID, db: Session = Depends(get_db)) -> CardOut:
    result = queries.get_card(db, card_id)
    if result is None:
        raise _not_found("Carta")
    return result


@router.get("/printings/{printing_id}", response_model=PrintingOut)
def printing(printing_id: uuid.UUID, db: Session = Depends(get_db)) -> PrintingOut:
    result = queries.get_printing(db, printing_id)
    if result is None:
        raise _not_found("Impresión")
    return result
