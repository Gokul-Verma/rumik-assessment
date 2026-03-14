from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Session(BaseModel):
    id: str | None = Field(None, alias="_id")
    user_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    is_active: bool = True
    message_count: int = 0
    platform: str = "app"
    context_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    def to_doc(self) -> dict:
        doc = self.model_dump(by_alias=True, exclude={"id"})
        doc.pop("_id", None)
        return doc
