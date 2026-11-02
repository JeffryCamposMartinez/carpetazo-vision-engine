from enum import StrEnum

from pydantic import BaseModel


class ScanStatus(StrEnum):
    confirmed = "confirmed"
    probable = "probable"
    ambiguous = "ambiguous"
    unknown = "unknown"
    poor_quality = "poor_quality"
    not_a_card = "not_a_card"


class CardRef(BaseModel):
    id: str
    name: str


class PrintingRef(BaseModel):
    id: str
    set: str | None = None
    collector_number: str | None = None
    language: str | None = None
    variant: str | None = None


class Alternative(BaseModel):
    card: CardRef
    printing: PrintingRef


class Quality(BaseModel):
    """Valores medidos del fotograma. `perspective` queda en null hasta que exista detector."""

    sharpness: float | None = None  # varianza del laplaciano
    lighting: float | None = None  # brillo medio 0-255
    perspective: float | None = None


class ScanResponse(BaseModel):
    scan_id: str
    session_id: str
    status: ScanStatus
    card: CardRef | None = None
    printing: PrintingRef | None = None
    alternatives: list[Alternative] = []
    quality: Quality
    model_version: str | None = None
    # false mientras detección, embeddings y búsqueda no estén implementados.
    recognition_available: bool = False
    message: str | None = None
