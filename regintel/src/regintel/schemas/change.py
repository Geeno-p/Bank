from datetime import datetime

from pydantic import Field, computed_field

from regintel.schemas.common import MATERIAL_CHANGES, Base, ChangeType, utcnow


class ParameterChange(Base):
    name: str
    old: str | None
    new: str | None


class ChangeClassification(Base):
    """LLM output for one aligned clause pair."""
    change_type: ChangeType
    changed_parameters: list[ParameterChange] = Field(default_factory=list)
    summary: str
    confidence: float = Field(ge=0, le=1)


class ClauseChange(Base):
    id: str
    doc_id: str
    old_clause_id: str | None
    new_clause_id: str | None
    change_type: ChangeType
    similarity: float | None = None
    changed_parameters: list[ParameterChange] = Field(default_factory=list)
    summary: str = ""

    @computed_field
    @property
    def is_material(self) -> bool:
        return self.change_type in MATERIAL_CHANGES


class ChangeEvent(Base):
    id: str
    doc_id: str
    from_version: str | None
    to_version: str
    detected_at: datetime = Field(default_factory=utcnow)
    changes: list[ClauseChange] = Field(default_factory=list)

    @property
    def material_changes(self) -> list[ClauseChange]:
        return [c for c in self.changes if c.is_material]