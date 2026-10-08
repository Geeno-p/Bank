from datetime import date

import pytest

from regintel.engines.evidence_tests import as_of_from, load_manifest, run_tests
from regintel.engines.risk import score
from regintel.schemas import Control, GapType, Obligation, Parameter, Provenance, SourceSpan
from regintel.synthetic.generate import generate_all

P = Provenance(created_by="t")


def _s():
    return SourceSpan(doc_id="D", version="v1", clause_ref="1", quote="placeholder quote")


def ctl(cid, params=()):
    return Control(id=cid, policy_id="P", clause_id="CL", name=cid, objective="obj", description="desc",
                   control_type="DETECTIVE", nature="HYBRID", parameters=list(params), source=_s(),
                   confidence=0.9, provenance=P)


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    root = tmp_path_factory.mktemp("d")
    generate_all(root)
    return root


def test_planted_results_and_change_demo(data):
    man = load_manifest(data)
    as_of = as_of_from(man)
    ctls = [ctl("KYC-C07"), ctl("KYC-C09", [Parameter(name="periodicity", value="two", unit="years")]),
            ctl("DPR-C09"), ctl("ITO-C06"), ctl("KYC-C12")]
    r = {x.control_id: x for _, x in run_tests(ctls, man, as_of, "T", root=data)}
    assert (r["KYC-C07"].exceptions, r["KYC-C07"].passed) == (180, False)         # G04
    assert (r["KYC-C09"].exceptions, r["KYC-C09"].passed) == (37, True)           # baseline
    assert r["ITO-C06"].exceptions == 17 and not r["ITO-C06"].passed              # G10
    assert r["DPR-C09"].exceptions == 400 and r["KYC-C12"].passed                 # G08 design test
    demo = {x.control_id: x for _, x in run_tests(ctls, man, as_of, "T", {"KYC-C09": 365}, data)}
    assert demo["KYC-C09"].exceptions == 312 and not demo["KYC-C09"].passed


def test_risk_deterministic_and_ordered():
    o = Obligation(id="O", regulation_id="RBI-KYC-MD", clause_id="CL", actor="RE", modality="MUST",
                   action="upload KYC records", topics=["KYC"], source=_s(), confidence=0.9, provenance=P)
    d = date(2025, 3, 31)
    a, b = score(GapType.UNMAPPED, o, d), score(GapType.STALE_CONTROL, o, d)
    assert a == score(GapType.UNMAPPED, o, d) and a.score > b.score