from pydantic import Field

from regintel.schemas.common import Base, Coverage, Provenance, SourceSpan, Temporal


class CandidateJudgement(Base):
    """LLM judge output for one obligation-control candidate pair."""
    control_id: str = Field(description="Must be one of the candidate IDs provided")
    coverage: Coverage
    rationale: str
    obligation_quote: str
    control_quote: str
    missing_elements: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class MappingJudgement(Base):
    judgements: list[CandidateJudgement]


class ControlMapping(Temporal):
    id: str
    obligation_id: str
    control_id: str
    coverage: Coverage
    rationale: str
    obligation_span: SourceSpan
    control_span: SourceSpan
    missing_elements: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    provenance: Provenance

class ObligationCoverage(Base):
    """Roll-up per obligation: best coverage across all mapped controls."""
    obligation_id: str
    regulation_id: str
    clause_ref: str
    coverage: Coverage
    control_ids: list[str] = Field(default_factory=list)
    missing_elements: list[str] = Field(default_factory=list)
    rationale: str = ""