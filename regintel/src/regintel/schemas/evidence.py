from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, model_validator

from regintel.schemas.common import Base, Temporal, utcnow


class EvidenceKind(StrEnum):
    LOG = "LOG"
    REPORT = "REPORT"
    ATTESTATION = "ATTESTATION"
    CONFIG = "CONFIG"
    MINUTES = "MINUTES"


class TestType(StrEnum):
    DESIGN = "DESIGN"
    OPERATING = "OPERATING"


class Evidence(Temporal):
    id: str
    control_ids: list[str]
    title: str
    kind: EvidenceKind
    path: str
    period_start: date | None = None
    period_end: date | None = None
    collected_on: date
    row_count: int | None = None
    columns: list[str] = Field(default_factory=list)


class ControlTestResult(Base):          # not "TestResult" -> avoids pytest collection clash
    id: str
    control_id: str
    evidence_id: str | None
    test_type: TestType
    procedure: str
    code: str | None = None             # sandbox script used (auditable)
    population: int = 0
    sample_size: int = 0
    exceptions: int = 0
    exception_rate: float = 0.0
    tolerance: float = 0.05
    passed: bool = True
    details: str = ""
    executed_at: datetime = Field(default_factory=utcnow)
    run_id: str | None = None

    @model_validator(mode="after")
    def _derive(self) -> "ControlTestResult":
        if self.exceptions > max(self.sample_size, self.population):
            raise ValueError("exceptions cannot exceed tested items")
        base = self.sample_size or self.population
        self.exception_rate = round(self.exceptions / base, 4) if base else 0.0
        self.passed = self.exception_rate <= self.tolerance
        return self