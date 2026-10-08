import hashlib
import json
from datetime import date

import pandas as pd
import pytest

from regintel.schemas.common import GapType
from regintel.synthetic.evidence_gen import GENERATORS
from regintel.synthetic.gaps import CONTRADICTIONS, PLANTED_GAPS, WITHHELD_EVIDENCE
from regintel.synthetic.generate import generate_all
from regintel.synthetic.spec import POLICIES, all_controls, effective_evidence

AS_OF = date(2025, 3, 31)


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    root = tmp_path_factory.mktemp("data")
    generate_all(root, seed=42, as_of=AS_OF)
    return root


def _csv(out, name, **kw):
    return pd.read_csv(out / "evidence" / f"{name}.csv", **kw)


# ---------- spec integrity (no files needed) ----------
def test_spec_integrity():
    ids = [c.id for c in all_controls()]
    assert len(POLICIES) == 4 and len(ids) == 42 and len(set(ids)) == 42
    assert all(g.control_id is None or g.control_id in ids for g in PLANTED_GAPS)
    keys = {k for c in all_controls() for k in effective_evidence(c)}
    assert {k[4:] for k in keys if k.startswith("csv:")} == set(GENERATORS)
    assert WITHHELD_EVIDENCE <= keys


def test_gap_design_covers_everything():
    assert len(PLANTED_GAPS) == 10 and len(CONTRADICTIONS) == 2
    assert {g.gap_type for g in PLANTED_GAPS} == set(GapType)          # all 6 gap types
    gap_ctls = {g.control_id for g in PLANTED_GAPS if g.control_id}
    assert len(all_controls()) - len(gap_ctls) == 34                    # clean traps
    assert sum(not c.maps_to for c in all_controls()) == 7              # no-regulation traps


# ---------- generated artefacts ----------
def test_files_written(out):
    for p in POLICIES:
        assert (out / "policies" / f"{p.id}.docx").stat().st_size > 0
        assert (out / "policies" / f"{p.id}.md").exists()
    assert len(list((out / "evidence").glob("*.csv"))) == 11
    assert (out / "golden" / "answer_key.json").exists()


def test_kyc_planted_counts(out):
    df = _csv(out, "kyc_periodic_updation", parse_dates=["last_kyc_update_date"])
    age = (pd.Timestamp(AS_OF) - df["last_kyc_update_date"]).dt.days
    high, med = df["risk_category"] == "HIGH", df["risk_category"] == "MEDIUM"
    assert high.sum() == 1840
    assert (high & (age > 730)).sum() == 37          # baseline 2y rule
    assert (high & (age > 365)).sum() == 312         # hypothetical 1y rule
    assert (med & (age > 2922)).sum() == 860         # G01: overdue vs 8y ...
    assert (df.loc[med, "kyc_status"] == "CURRENT").all()   # ... but CURRENT per 10y policy


def test_operating_failures(out):
    s = _csv(out, "sanctions_screening_log")
    assert len(s) == 3000 and (~s["screened_before_activation"]).sum() == 180      # G04
    v = _csv(out, "vendor_privileged_access_review")
    unrev = ~v["reviewed_in_quarter"]
    assert len(v) == 120 and unrev.sum() == 17                                      # G10
    assert (unrev & ~v["still_required"]).sum() == 5


def test_other_signals(out):
    assert (_csv(out, "consent_records")["status"] == "WITHDRAWN").sum() == 0       # G06
    e = _csv(out, "data_erasure_log")
    assert e["data_categories_erased"].str.contains("KYC_DOCUMENTS").all()          # G08
    assert (e["days_after_closure"] < 5 * 365).all()
    i = _csv(out, "security_incidents")
    pdi = i[i["personal_data_involved"]]
    assert len(pdi) == 4 and (pdi["notified_to"] == "CERT-In;RBI").all()            # G05


def test_manifest_withholds_evidence(out):
    m = json.loads((out / "evidence" / "manifest.json").read_text(encoding="utf-8"))
    covered = {cid for e in m for cid in e["control_ids"]}
    assert "KYC-C06" not in covered and "ITO-C08" not in covered                    # G03, G09
    assert len(m) == 33                                                             # 11 CSV + 22 att
    assert "Last design review of this control: 2021-08-16" in (
        out / "evidence" / "attestations" / "dpr-c08.md").read_text(encoding="utf-8")  # G07


def test_policy_text_has_planted_parameters(out):
    kyc = (out / "policies" / "KYC-POL.md").read_text(encoding="utf-8")
    assert "once every ten years for Medium risk" in kyc
    dpr = (out / "policies" / "DPR-POL.md").read_text(encoding="utf-8")
    assert "within 180 days of account closure" in dpr and "August 2021" in dpr


def test_answer_key_change_demo(out):
    k = json.loads((out / "golden" / "answer_key.json").read_text(encoding="utf-8"))
    assert k["summary"]["controls"] == 42 and k["summary"]["attestations"] == 22
    cd = k["change_demo"]
    assert cd["baseline"]["overdue"] == 37 and cd["baseline"]["passes_tolerance"]
    assert cd["hypothetical"]["overdue"] == 312 and not cd["hypothetical"]["passes_tolerance"]


def test_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for r in (a, b):
        generate_all(r, seed=42, as_of=AS_OF)

    def digest(root):  # .docx excluded: zip entries carry write timestamps
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file() and p.suffix != ".docx"}

    assert digest(a) == digest(b)