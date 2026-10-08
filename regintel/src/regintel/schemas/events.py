from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from regintel.schemas.common import Base, utcnow


class EventType(StrEnum):
    NEW_DOC = "NEW_DOC"
    NEW_VERSION = "NEW_VERSION"
    POLICY_UPDATE = "POLICY_UPDATE"
    EVIDENCE_UPLOADED = "EVIDENCE_UPLOADED"
    SCHEDULED_TEST = "SCHEDULED_TEST"
    USER_QUERY = "USER_QUERY"
    WHAT_IF = "WHAT_IF"                # reserved, Phase 7


class Event(Base):
    id: str
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "system"
    created_at: datetime = Field(default_factory=utcnow)