from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(BaseModel):
    id: str | None = Field(None, alias="_id")
    session_id: str
    user_id: str
    role: Role = Role.USER
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    processing_time_ms: float = 0.0
    tokens_used: int = 0
    safety_flagged: bool = False
    rate_limited: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    def to_doc(self) -> dict:
        doc = self.model_dump(by_alias=True, exclude={"id"})
        doc.pop("_id", None)
        return doc
