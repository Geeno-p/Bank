from pydantic import Field

from regintel.schemas.common import (
    Base, Frequency, Modality, Parameter, Provenance, SourceSpan, Temporal,
)


class ObligationDraft(Base):
    """LLM output. No IDs. Must quote verbatim."""
    actor: str = Field(description="Who must comply, e.g. 'Regulated Entity', 'Board'")
    modality: Modality
    action: str = Field(description="What must / must not be done, imperative form")
    condition: str | None = Field(None, description="Trigger or scope condition, if any")
    frequency: Frequency | None = None
    parameters: list[Parameter] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list, description="Taxonomy topic IDs")
    penalty_ref: str | None = None
    quote: str = Field(description="Exact verbatim sentence(s) from the clause")
    confidence: float = Field(ge=0, le=1)


class ObligationExtraction(Base):
    """One LLM call per clause -> 0..n obligations."""
    obligations: list[ObligationDraft] = Field(default_factory=list)
    is_non_obligatory: bool = Field(
        False, description="True if clause is definitional / preamble / no duty"
    )


class Obligation(Temporal):
    id: str
    regulation_id: str
    clause_id: str
    actor: str
    modality: Modality
    action: str
    condition: str | None = None
    frequency: Frequency | None = None
    parameters: list[Parameter] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    penalty_ref: str | None = None
    source: SourceSpan
    confidence: float = Field(ge=0, le=1)
    provenance: Provenance
    supersedes_id: str | None = None

    def summary(self) -> str:
        cond = f" when {self.condition}" if self.condition else ""
        return f"{self.actor} {self.modality.value} {self.action}{cond}"


class ApplicabilityDecision(Base):
    obligation_id: str
    applicable: bool
    scope: list[str] = Field(default_factory=list, description="Entity/product IDs in scope")
    reason: str
    confidence: float = Field(ge=0, le=1)
    decided_by: str = Field(description="'rule:<name>' or 'agent:applicability'")