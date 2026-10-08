from enum import StrEnum

from pydantic import Field

from regintel.schemas.common import Base, SourceSpan


class RelationType(StrEnum):
    CONFLICT = "CONFLICT"
    OVERLAP = "OVERLAP"
    A_STRICTER = "A_STRICTER"
    B_STRICTER = "B_STRICTER"
    RELATED = "RELATED"
    INDEPENDENT = "INDEPENDENT"


class ResolutionPrinciple(StrEnum):
    STRICTEST_WINS = "STRICTEST_WINS"
    LEX_SPECIALIS = "LEX_SPECIALIS"
    STATUTORY_OVERRIDE = "STATUTORY_OVERRIDE"
    SEQUENTIAL = "SEQUENTIAL"          # e.g. retain for AML period, THEN erase
    ESCALATE_LEGAL = "ESCALATE_LEGAL"


class RelationJudgement(Base):
    """LLM output for an obligation pair."""
    relation: RelationType
    explanation: str
    resolution_principle: ResolutionPrinciple | None = None
    harmonised_obligation: str | None = None
    confidence: float = Field(ge=0, le=1)


class RegRelation(Base):
    id: str
    obligation_a_id: str
    obligation_b_id: str
    relation: RelationType
    explanation: str
    span_a: SourceSpan
    span_b: SourceSpan
    resolution_principle: ResolutionPrinciple | None = None
    harmonised_obligation: str | None = None
    confidence: float = Field(ge=0, le=1)