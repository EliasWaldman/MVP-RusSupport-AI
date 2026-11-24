"""Схемы сообщений рабочих чатов."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MessageRole = Literal["employee", "manager", "courier", "recipient", "support", "system"]


class MessageCreate(BaseModel):
    """Входящее сообщение из чата."""

    model_config = ConfigDict(str_strip_whitespace=True)

    role: MessageRole = "support"
    author: str = Field(default="", max_length=255)
    text: str = Field(..., min_length=1, max_length=4000)
    created_at: datetime | None = None


class MessageRead(BaseModel):
    """Сообщение чата для API и дашборда."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    case_code: str
    role: MessageRole
    author: str
    text: str
    created_at: datetime | None = None
