from datetime import datetime
from enum import StrEnum

from pydantic import Field

from regintel.schemas.common import Base, utcnow


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"


class ReviewItem(Base):
    id: str
    item_type: str            # "obligation" | "mapping" | "gap" | "remediation" | "applicability"
    item_id: str
    reason: str               # "LOW_CONFIDENCE" | "CRITICAL_RISK" | "SPAN_MISMATCH" ...
    confidence: float | None = None
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    decision_notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)