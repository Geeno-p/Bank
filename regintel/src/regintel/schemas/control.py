from pydantic import Field

from regintel.schemas.common import (
    Base, ControlNature, ControlType, Frequency, Parameter, Provenance, SourceSpan, Temporal,
)


class Policy(Temporal):
    id: str
    title: str
    owner_role: str
    version_label: str
    doc_id: str
    topics: list[str] = Field(default_factory=list)


class ControlDraft(Base):
    """LLM output from a policy clause."""
    name: str
    objective: str
    description: str
    control_type: ControlType
    nature: ControlNature
    frequency: Frequency | None = None
    parameters: list[Parameter] = Field(default_factory=list)
    owner_role: str | None = None
    systems: list[str] = Field(default_factory=list)
    evidence_expected: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    quote: str
    confidence: float = Field(ge=0, le=1)


class ControlExtraction(Base):
    controls: list[ControlDraft] = Field(default_factory=list)


class Control(Temporal):
    id: str
    policy_id: str
    clause_id: str
    name: str
    objective: str
    description: str
    control_type: ControlType
    nature: ControlNature
    frequency: Frequency | None = None
    parameters: list[Parameter] = Field(default_factory=list)
    owner_role: str | None = None
    systems: list[str] = Field(default_factory=list)
    evidence_expected: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    source: SourceSpan
    confidence: float = Field(ge=0, le=1)
    provenance: Provenance
    design_effectiveness: float | None = Field(None, ge=0, le=1)
    operating_effectiveness: float | None = Field(None, ge=0, le=1)