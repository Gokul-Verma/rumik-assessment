from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Tier(StrEnum):
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class Platform(StrEnum):
    WHATSAPP = "whatsapp"
    APP = "app"


class User(BaseModel):
    id: str | None = Field(None, alias="_id")
    external_id: str
    phone: str
    display_name: str
    tier: Tier = Tier.FREE
    platform: Platform = Platform.APP
    language: str = "en"
    timezone: str = "UTC"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    def to_doc(self) -> dict:
        doc = self.model_dump(by_alias=True, exclude={"id"})
        doc.pop("_id", None)
        return doc
