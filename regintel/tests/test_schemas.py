from datetime import date

import pytest
from pydantic import ValidationError

from regintel.schemas import (
    ChangeType, ClauseChange, ControlTestResult, Obligation, Provenance,
    SourceSpan, TestType, Modality, stable_id,
)


def _span() -> SourceSpan:
    return SourceSpan(doc_id="RBI-KYC-MD", version="v1", clause_ref="38(a)",
                      quote="at least once in every two years", char_start=10, char_end=42)


def test_stable_id_is_deterministic():
    assert stable_id("OBL", "a", "b") == stable_id("OBL", "a", "b")
    assert stable_id("OBL", "a", "b") != stable_id("OBL", "a", "c")


def test_span_offsets_validated():
    with pytest.raises(ValidationError):
        SourceSpan(doc_id="d", version="v", clause_ref="c", quote="abc", char_start=10, char_end=5)


def test_temporal_validity_and_active():
    o = Obligation(
        id="OBL-1", regulation_id="RBI-KYC-MD", clause_id="CL-1", actor="Regulated Entity",
        modality=Modality.MUST, action="carry out periodic KYC updation", source=_span(),
        confidence=0.9, provenance=Provenance(created_by="agent:test"),
        valid_from=date(2024, 1, 1), valid_to=date(2025, 1, 1),
    )
    assert o.is_active(date(2024, 6, 1))
    assert not o.is_active(date(2025, 6, 1))
    with pytest.raises(ValidationError):
        o.model_validate({**o.model_dump(), "valid_to": date(2023, 1, 1)})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        SourceSpan(doc_id="d", version="v", clause_ref="c", quote="abc", hallucinated="x")


def test_control_test_result_derivation():
    r = ControlTestResult(id="T1", control_id="C1", evidence_id="E1",
                          test_type=TestType.OPERATING, procedure="overdue re-KYC",
                          population=1840, exceptions=312, tolerance=0.05)
    assert r.exception_rate == pytest.approx(0.1696, abs=1e-3)
    assert r.passed is False


def test_change_materiality():
    c = ClauseChange(id="CH1", doc_id="RBI-KYC-MD", old_clause_id="a", new_clause_id="b",
                     change_type=ChangeType.TIGHTENED)
    assert c.is_material
    c2 = c.model_copy(update={"change_type": ChangeType.EDITORIAL})
    assert not c2.is_material