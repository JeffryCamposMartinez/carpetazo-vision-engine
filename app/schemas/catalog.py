from datetime import date

from pydantic import BaseModel


class PrintingOut(BaseModel):
    id: str
    card_id: str
    card_name: str
    set_id: str
    set_name: str
    set_code: str | None = None
    collector_number: str | None = None
    language: str
    rarity: str | None = None
    variant: str | None = None
    has_image: bool


class CardOut(BaseModel):
    id: str
    name: str
    printings: list[PrintingOut]


class SetOut(BaseModel):
    id: str
    name: str
    code: str | None = None
    language: str
    release_date: date | None = None
