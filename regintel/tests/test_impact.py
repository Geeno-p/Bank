from datetime import date

from regintel.agents import impact as imp
from regintel.schemas import ControlMapping, Gap
from regintel.schemas.change import ChangeEvent, ClauseChange, ParameterChange
from regintel.schemas.common import DocType
from regintel.schemas.regulation import DocVersion
from test_phase3b import P, _s, ctl, obl

EV = ChangeEvent(id="CHG-1", doc_id="R", from_version="v1", to_version="v2", changes=[
    ClauseChange(id="CC1", doc_id="R", old_clause_id="CLold", new_clause_id="CLo", change_type="TIGHTENED",
                 summary="yearly", changed_parameters=[ParameterChange(name="periodicity", old="two years", new="one year")])])
VER = DocVersion(id="V2", doc_id="R", doc_type=DocType.REGULATION, version_label="v2", source_path="x",
                 sha256="x", effective_date=date(2025, 7, 1))


def _state():
    old = obl("O1", "update high risk KYC every two years").model_copy(update={"clause_id": "CLold", "valid_to": date(2025, 7, 1)})
    new = obl("O2", "update high risk KYC every year")                                   # clause CLo
    other = obl("O3", "record customer risk category").model_copy(update={"clause_id": "CLx", "regulation_id": "R2"})
    c = ctl("C1", "re-KYC high risk").model_copy(update={"owner_role": "Head of KYC Ops", "systems": ["CBS"]})

    def m(oid, cov):
        return ControlMapping(id=f"M-{oid}", obligation_id=oid, control_id="C1", coverage=cov, rationale="r",
                              obligation_span=_s("q q q"), control_span=_s("q q q"), confidence=0.9, provenance=P)

    g = Gap(id="GAP-1", gap_type="DESIGN_GAP", obligation_id="O2", control_id="C1", title="t", description="d",
            provenance=P, metrics={"threshold_days": 365, "exceptions": 312, "population": 1840, "exception_rate": 0.1696})
    return imp.State([old, new, other], [c], [m("O2", "PARTIAL"), m("O3", "FULL")], [g], [], [],
                     {"CLo": "12(a)", "CLold": "12(a)"})


def test_build_report_traces_change_to_controls_owners_and_exposure():
    r = imp.build_report(EV, VER, _state(), date(2025, 3, 31))
    ch = r.changes[0]
    assert ch.retired_obligation_ids == ["O1"] and ch.new_obligation_ids == ["O2"]
    assert ch.controls[0].also_covers == ["R2 1"]                 # ripple into another regulation
    assert r.owners == ["Head of KYC Ops"] and r.systems == ["CBS"] and r.days_to_effective == 92
    assert "312/1840 (17.0%)" in r.exposure[0] and not r.issues


def test_narrative_revises_ungrounded_numbers():
    r = imp.build_report(EV, VER, _state(), date(2025, 3, 31))
    drafts = iter([{"headline": "5000 customers at risk", "executive_summary": "s"},
                   {"headline": "312 of 1840 high-risk customers overdue", "executive_summary": "Effective in 92 days."}])
    out = imp.write_narrative(r, llm=lambda t, m, s, u: m(**next(drafts)))
    assert out.narrative.headline.startswith("312") and not out.issues