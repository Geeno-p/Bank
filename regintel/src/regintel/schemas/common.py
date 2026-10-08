from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stable_id(prefix: str, *parts: str, length: int = 12) -> str:
    """Deterministic ID: same inputs -> same ID (idempotent re-ingestion)."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def content_hash(text: str) -> str:
    normalised = " ".join(text.split()).lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------- Enums ----------
class DocType(StrEnum):
    REGULATION = "REGULATION"
    POLICY = "POLICY"
    EVIDENCE = "EVIDENCE"


class Modality(StrEnum):
    MUST = "MUST"
    MUST_NOT = "MUST_NOT"
    SHOULD = "SHOULD"
    MAY = "MAY"


class Coverage(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class ChangeType(StrEnum):
    NEW = "NEW"
    DELETED = "DELETED"
    TIGHTENED = "TIGHTENED"
    RELAXED = "RELAXED"
    CLARIFICATION = "CLARIFICATION"
    EDITORIAL = "EDITORIAL"
    UNCHANGED = "UNCHANGED"


MATERIAL_CHANGES = {ChangeType.NEW, ChangeType.DELETED, ChangeType.TIGHTENED, ChangeType.RELAXED}


class GapType(StrEnum):
    UNMAPPED = "UNMAPPED"
    PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
    DESIGN_GAP = "DESIGN_GAP"
    OPERATING_FAILURE = "OPERATING_FAILURE"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    STALE_CONTROL = "STALE_CONTROL"


class RiskBand(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ControlType(StrEnum):
    PREVENTIVE = "PREVENTIVE"
    DETECTIVE = "DETECTIVE"
    CORRECTIVE = "CORRECTIVE"


class ControlNature(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATED = "AUTOMATED"
    HYBRID = "HYBRID"


class Frequency(StrEnum):
    CONTINUOUS = "CONTINUOUS"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    ANNUAL = "ANNUAL"
    BIENNIAL = "BIENNIAL"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    AD_HOC = "AD_HOC"


FREQUENCY_DAYS: dict[Frequency, int | None] = {
    Frequency.CONTINUOUS: 1, Frequency.DAILY: 1, Frequency.WEEKLY: 7,
    Frequency.MONTHLY: 30, Frequency.QUARTERLY: 91, Frequency.SEMI_ANNUAL: 182,
    Frequency.ANNUAL: 365, Frequency.BIENNIAL: 730,
    Frequency.EVENT_DRIVEN: None, Frequency.AD_HOC: None,
}


# ---------- Shared value objects ----------
class SourceSpan(Base):
    """Exact pointer into a source document. The backbone of traceability."""
    doc_id: str
    version: str
    clause_ref: str
    quote: str = Field(min_length=3, description="Verbatim text from the source")
    char_start: int | None = None
    char_end: int | None = None
    page: int | None = None

    @model_validator(mode="after")
    def _offsets(self) -> "SourceSpan":
        if self.char_start is not None and self.char_end is not None:
            if self.char_end < self.char_start:
                raise ValueError("char_end must be >= char_start")
        return self


class Parameter(Base):
    """Machine-comparable parameter, e.g. frequency=12 months. Powers change diffs."""
    name: str
    value: str
    unit: str | None = None


class Provenance(Base):
    created_by: str = Field(description="'agent:<name>', 'rule:<name>' or 'human:<id>'")
    run_id: str | None = None
    model: str | None = None
    prompt_version: str | None = None


class Temporal(Base):
    """Bitemporal + scenario-ready base for every graph entity."""
    valid_from: date | None = None
    valid_to: date | None = None
    recorded_at: datetime = Field(default_factory=utcnow)
    scenario_id: str | None = None          # reserved for Phase 7 (what-if)

    @model_validator(mode="after")
    def _validity(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be >= valid_from")
        return self

    def is_active(self, on: date | None = None) -> bool:
        on = on or date.today()
        if self.valid_from and on < self.valid_from:
            return False
        return not (self.valid_to and on >= self.valid_to)