from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    # Referencia opaca del usuario en Carpetazo; el Scanner no la interpreta ni la guarda.
    user_ref: str | None = Field(default=None, max_length=64)


class SessionResponse(BaseModel):
    session_id: str
    token: str
    expires_at: datetime
