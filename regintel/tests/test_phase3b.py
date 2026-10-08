import numpy as np

from regintel.agents import mapping as mp
from regintel.schemas import ApplicabilityDecision, Control, Obligation, Parameter, Provenance, SourceSpan
from regintel.schemas.mapping import CandidateJudgement

P = Provenance(created_by="t")
Y8 = Parameter(name="periodicity", value="8", unit="years")
Y10 = Parameter(name="periodicity", value="ten", unit="years")


def _s(q):
    return SourceSpan(doc_id="D", version="v1", clause_ref="1", quote=q)


def obl(oid, action, params=()):
    return Obligation(id=oid, regulation_id="R", clause_id="CLo", actor="RE", modality="MUST", action=action,
                      topics=["KYC"], parameters=list(params), source=_s(action), confidence=0.9, provenance=P)


def ctl(cid, text, params=()):
    return Control(id=cid, policy_id="P", clause_id="CLc", name=text, objective=text, description=text,
                   control_type="DETECTIVE", nature="HYBRID", topics=["KYC"], parameters=list(params),
                   source=_s(text), confidence=0.9, provenance=P)


def test_param_guard_downgrades_full_on_period_mismatch():
    j = CandidateJudgement(control_id="C1", coverage="FULL", rationale="r", obligation_quote="q",
                           control_quote="q", confidence=0.9)
    out = mp.param_guard(obl("O1", "update medium risk KYC", [Y8]), ctl("C1", "re-KYC medium", [Y10]), j)
    assert out.coverage == "PARTIAL" and "mismatch" in out.missing_elements[0].lower()
    same = mp.param_guard(obl("O1", "update medium risk KYC", [Y10]), ctl("C1", "re-KYC medium", [Y10]), j)
    assert same.coverage == "FULL"


def test_map_obligations_with_fake_llm():
    obls = [obl("O1", "update medium risk KYC", [Y8]), obl("O2", "upload KYC records to CKYCR")]
    ctls = [ctl("C1", "re-KYC medium risk", [Y10]), ctl("C2", "sanctions screening")]
    decs = [ApplicabilityDecision(obligation_id=o.id, applicable=True, reason="r", confidence=0.9,
                                  decided_by="rule:t") for o in obls]

    def fake(task, model, system, user):
        j = {"rationale": "r", "obligation_quote": "x", "control_quote": "y", "confidence": 0.9}
        return model(results=[
            {"obligation_key": "O1", "judgements": [{**j, "control_id": "C1", "coverage": "FULL"},
                                                    {**j, "control_id": "BOGUS", "coverage": "FULL"}]},
            {"obligation_key": "O2", "judgements": []}])

    idx = mp.ControlIndex(ctls, embed=lambda t: np.ones((len(t), 2)) / np.sqrt(2))
    maps, cov, stats = mp.map_obligations(obls, ctls, decs, "RUN-T", llm=fake, index=idx, lookup={})
    assert {c.obligation_id: c.coverage.value for c in cov} == {"O1": "PARTIAL", "O2": "NONE"}
    assert stats["invalid_ids"] == 1 and stats["guard_downgrades"] == 1
    assert maps[0].confidence <= mp.UNVERIFIED_CAP          # unverified quotes -> review