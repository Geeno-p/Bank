import numpy as np

from regintel.agents.applicability import decide
from regintel.config import get_bank_profile
from regintel.retrieval.hybrid import ControlIndex
from regintel.schemas import Control, Obligation, Provenance, SourceSpan

VOCAB = ["kyc", "updation", "sanctions", "screening", "consent", "breach", "vendor", "access"]


def fake_embed(texts):
    m = np.array([[t.lower().count(w) for w in VOCAB] for t in texts], float) + 1e-6
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def _span(q):
    return SourceSpan(doc_id="D", version="v1", clause_ref="1", quote=q)


def ctl(cid, text, topics):
    return Control(id=cid, policy_id="P", clause_id="CL", name=text, objective=text, description=text,
                   control_type="DETECTIVE", nature="MANUAL", topics=topics, source=_span(text),
                   confidence=0.9, provenance=Provenance(created_by="t"))


def obl(action, actor="Regulated Entity", topics=("KYC",), quote=None):
    return Obligation(id="O1", regulation_id="R", clause_id="CL", actor=actor, modality="MUST",
                      action=action, topics=list(topics), source=_span(quote or action),
                      confidence=0.9, provenance=Provenance(created_by="t"))


def test_hybrid_ranks_relevant_control_first():
    idx = ControlIndex([ctl("C1", "Periodic KYC updation of customers", ["KYC"]),
                        ctl("C2", "Sanctions screening before account activation", ["AML_CFT"]),
                        ctl("C3", "Consent capture and logging", ["CONSENT"])], embed=fake_embed)
    top = idx.search(obl("screen customer against sanctions lists before opening account",
                         topics=["AML_CFT"]), k=2)
    assert top[0].control.id == "C2"


def test_applicability_rules():
    bp = get_bank_profile()
    assert not decide(obl("notify the rules", actor="Central Government"), bp).applicable
    assert not decide(obl("maintain payments bank deposit limits"), bp).applicable
    sdf = decide(obl("appoint a Data Protection Officer", quote="A Significant Data Fiduciary shall appoint"), bp)
    assert sdf.applicable and sdf.confidence < 0.8
    d = decide(obl("carry out periodic KYC updation"), bp)
    assert d.applicable and d.decided_by == "rule:default" and d.scope