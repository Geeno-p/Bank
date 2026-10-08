"""Deterministic evidence test library (Phase 4-lite: fixed pandas procedures; no LLM, no sandbox)."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from regintel.agents.mapping import to_days
from regintel.config import get_risk_weights
from regintel.schemas.common import stable_id
from regintel.schemas.control import Control
from regintel.schemas.evidence import ControlTestResult, Evidence, TestType
from regintel.settings import get_settings

Masks = tuple[pd.Series, pd.Series]  # (population, exceptions)


@dataclass(frozen=True)
class Procedure:
    control_id: str
    evidence: str                       # CSV stem in the manifest
    description: str
    fn: Callable[[pd.DataFrame, date, float | None], Masks]
    test_type: TestType = TestType.OPERATING
    default_days: float | None = None
    use_control_param: bool = False     # threshold read from the control's extracted parameters
    requirement: str | None = None      # links a failing test to the obligation it protects


def _all(df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=df.index)


def _b(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().eq("true")


def _age(df: pd.DataFrame, col: str, as_of: date) -> pd.Series:
    return (pd.Timestamp(as_of) - pd.to_datetime(df[col], errors="coerce")).dt.days


def _overdue(risk: str):
    return lambda df, a, d: (df["risk_category"].eq(risk), _age(df, "last_kyc_update_date", a) > d)


P = Procedure
LIBRARY: list[Procedure] = [
    P("KYC-C03", "cdd_onboarding_log", "Every activated account has a VERIFIED identity record",
      lambda df, a, d: (_all(df), df["verification_status"].ne("VERIFIED"))),
    P("KYC-C04", "vcip_sessions", "V-CIP sessions recorded, geo-tagged and liveness-checked",
      lambda df, a, d: (_all(df), ~(_b(df["recorded"]) & _b(df["geo_tagged"]) & _b(df["liveness_passed"])))),
    P("KYC-C05", "cdd_onboarding_log", "Identity verified before account activation",
      lambda df, a, d: (_all(df), pd.to_datetime(df["verified_at"]) > pd.to_datetime(df["activated_at"]))),
    P("KYC-C07", "sanctions_screening_log", "Sanctions screening cleared before activation",
      lambda df, a, d: (_all(df), ~_b(df["screened_before_activation"]))),
    P("KYC-C09", "kyc_periodic_updation", "High-risk customers re-KYC'd within the period",
      _overdue("HIGH"), default_days=730, use_control_param=True),
    P("KYC-C10", "kyc_periodic_updation", "Medium-risk customers re-KYC'd within the period",
      _overdue("MEDIUM"), default_days=3650, use_control_param=True),
    P("KYC-C11", "kyc_periodic_updation", "Low-risk customers re-KYC'd within the period",
      _overdue("LOW"), default_days=3650, use_control_param=True),
    P("KYC-C12", "str_filing_log", "STRs filed within 7 working days of determination",
      lambda df, a, d: (_all(df), df["working_days_to_file"] > 7)),
    P("DPR-C03", "consent_records", "Consent captured with a versioned notice",
      lambda df, a, d: (_all(df), df["notice_version"].isna())),
    P("DPR-C04", "consent_records", "Consent captured by clear affirmative action",
      lambda df, a, d: (_all(df), df["capture_method"].ne("AFFIRMATIVE_CHECKBOX"))),
    P("DPR-C09", "data_erasure_log", "KYC documents not erased before the 5-year statutory retention",
      lambda df, a, d: (df["data_categories_erased"].str.contains("KYC_DOCUMENTS", na=False),
                        df["days_after_closure"] < d),
      test_type=TestType.DESIGN, default_days=1826,
      requirement="preserve records of identification of customers for at least five years after "
                  "the business relationship has ended"),
    P("ITO-C02", "vendor_register", "All IT service providers in the outsourcing register",
      lambda df, a, d: (_all(df), ~_b(df["in_outsourcing_register"]))),
    P("ITO-C03", "vendor_register", "Due diligence completed before onboarding",
      lambda df, a, d: (_all(df), pd.to_datetime(df["due_diligence_completed_on"]) > pd.to_datetime(df["onboarded_on"]))),
    P("ITO-C05", "vendor_register", "Material providers risk-assessed within 12 months",
      lambda df, a, d: (_b(df["material"]), _age(df, "last_risk_assessment_on", a).fillna(99999) > d),
      default_days=365),
    P("ITO-C06", "vendor_privileged_access_review", "Service-provider privileged access reviewed this quarter",
      lambda df, a, d: (_all(df), ~_b(df["reviewed_in_quarter"]))),
    P("ISB-C03", "access_reviews", "Internal privileged accounts reviewed this quarter",
      lambda df, a, d: (_all(df), ~_b(df["reviewed_in_quarter"]))),
    P("ISB-C08", "security_incidents", "Incidents reported to CERT-In within 6 hours",
      lambda df, a, d: (_all(df), df["hours_to_certin"] > 6)),
]


def load_manifest(root: Path | None = None) -> list[Evidence]:
    root = root or get_settings().data_dir
    raw = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
    return [Evidence.model_validate(x) for x in raw]


def as_of_from(manifest: list[Evidence]) -> date:
    return max(e.collected_on for e in manifest)


def _days(p: Procedure, ctl: Control, overrides: dict[str, float]) -> float | None:
    if p.control_id in overrides:
        return overrides[p.control_id]
    if p.use_control_param:
        for prm in ctl.parameters:
            if (d := to_days(prm)) is not None:
                return d
    return p.default_days


def run_tests(controls: list[Control], manifest: list[Evidence], as_of: date, run_id: str,
              overrides: dict[str, float] | None = None,
              root: Path | None = None) -> list[tuple[Procedure, ControlTestResult]]:
    root, overrides = root or get_settings().data_dir, overrides or {}
    tol = get_risk_weights().get("default_exception_tolerance", 0.05)
    files = {Path(e.path).stem: e for e in manifest if e.path.endswith(".csv")}
    by_id, frames, out = {c.id: c for c in controls}, {}, []
    for p in LIBRARY:
        if p.control_id not in by_id or p.evidence not in files:
            continue
        ev = files[p.evidence]
        if p.evidence not in frames:
            frames[p.evidence] = pd.read_csv(root / ev.path)
        df, days = frames[p.evidence], _days(p, by_id[p.control_id], overrides)
        pop, exc = p.fn(df, as_of, days)
        n, x = int(pop.sum()), int((pop & exc).sum())
        detail = f"{x}/{n} exceptions" + (f" (threshold {days:g} days)" if days else "")
        out.append((p, ControlTestResult(
            id=stable_id("TST", p.control_id, str(days), as_of.isoformat()), control_id=p.control_id,
            evidence_id=ev.id, test_type=p.test_type, procedure=p.description, population=n, exceptions=x,
            tolerance=0.0 if p.test_type == TestType.DESIGN else tol, details=detail, run_id=run_id)))
    return out