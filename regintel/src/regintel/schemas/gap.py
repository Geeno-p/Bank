from enum import StrEnum

from pydantic import Field

from regintel.schemas.common import Base, GapType, Provenance, RiskBand, Temporal


class GapStatus(StrEnum):
    OPEN = "OPEN"
    IN_REMEDIATION = "IN_REMEDIATION"
    CLOSED = "CLOSED"
    RISK_ACCEPTED = "RISK_ACCEPTED"


class RiskFactors(Base):
    severity: float = Field(ge=0)
    inherent: float = Field(ge=0)
    control_weakness: float = Field(ge=0, le=1)
    urgency: float = Field(ge=0)
    blast_radius: float = Field(ge=0)


class RiskScore(Base):
    factors: RiskFactors
    score: float
    band: RiskBand
    explanation: str | None = None       # LLM explains; never computes


class Gap(Temporal):
    id: str
    gap_type: GapType
    obligation_id: str
    control_id: str | None = None
    title: str
    description: str
    evidence_refs: list[str] = Field(default_factory=list)
    risk: RiskScore | None = None
    status: GapStatus = GapStatus.OPEN
    provenance: Provenance


class ActionType(StrEnum):
    POLICY_UPDATE = "POLICY_UPDATE"
    NEW_CONTROL = "NEW_CONTROL"
    CONTROL_ENHANCEMENT = "CONTROL_ENHANCEMENT"
    PROCESS_CHANGE = "PROCESS_CHANGE"
    SYSTEM_CHANGE = "SYSTEM_CHANGE"
    TRAINING = "TRAINING"
    EVIDENCE_COLLECTION = "EVIDENCE_COLLECTION"


class RemediationAction(Base):
    action_type: ActionType
    description: str
    draft_text: str | None = None
    owner_role: str
    effort: str = Field(pattern="^(S|M|L)$")
    due_in_days: int = Field(gt=0)


class RemediationDraft(Base):
    """LLM output."""
    actions: list[RemediationAction]
    rationale: str
    expected_residual_band: RiskBand


class Remediation(Temporal):
    id: str
    gap_id: str
    actions: list[RemediationAction]
    rationale: str
    expected_residual_band: RiskBand
    status: str = "PROPOSED"
    provenance: Provenance