from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Tone(StrEnum):
    FRIENDLY = "friendly"
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    EMPATHETIC = "empathetic"


class Verbosity(StrEnum):
    CONCISE = "concise"
    NORMAL = "normal"
    DETAILED = "detailed"


class Personality(BaseModel):
    id: str | None = Field(None, alias="_id")
    user_id: str
    tone: Tone = Tone.FRIENDLY
    verbosity: Verbosity = Verbosity.NORMAL
    humor_level: int = Field(default=5, ge=0, le=10)
    formality: int = Field(default=5, ge=0, le=10)
    interests: list[str] = Field(default_factory=list)
    custom_instructions: str = ""
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}

    def to_doc(self) -> dict:
        doc = self.model_dump(by_alias=True, exclude={"id"})
        doc.pop("_id", None)
        return doc
