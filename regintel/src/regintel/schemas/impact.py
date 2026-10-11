"""Impact analysis schemas (Phase 6B)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from regintel.schemas.change import ParameterChange
from regintel.schemas.common import Base, ChangeType, Coverage, RiskBand, utcnow


class ImpactedControl(Base):
    control_id: str
    name: str
    coverage: Coverage
    owner_role: str | None = None
    systems: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    test: str | None = None
    test_passed: bool | None = None
    also_covers: list[str] = Field(default_factory=list, description="Other active obligations this control serves")


class ImpactedGap(Base):
    gap_id: str
    gap_type: str
    band: str | None = None
    score: float | None = None
    title: str


class ChangeImpact(Base):
    clause_ref: str
    change_type: ChangeType
    changed_parameters: list[ParameterChange] = Field(default_factory=list)
    summary: str = ""
    retired_obligation_ids: list[str] = Field(default_factory=list)
    new_obligation_ids: list[str] = Field(default_factory=list)
    controls: list[ImpactedControl] = Field(default_factory=list)
    gaps: list[ImpactedGap] = Field(default_factory=list)


class ImpactNarrative(Base):
    """LLM output."""
    headline: str
    executive_summary: str
    key_risks: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)


class ImpactReport(Base):
    id: str
    change_event_id: str
    regulation_id: str
    from_version: str | None = None
    to_version: str
    effective_date: date | None = None
    as_of: date
    days_to_effective: int | None = None
    changes: list[ChangeImpact] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    exposure: list[str] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
    max_band: RiskBand | None = None
    narrative: ImpactNarrative | None = None
    issues: list[str] = Field(default_factory=list)
    run_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)