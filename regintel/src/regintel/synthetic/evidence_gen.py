"""Deterministic evidence generator with exact planted exceptions."""
"""Deterministic evidence generator with exact planted exceptions."""
#from __future__ import annotations
import csv
import json
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from regintel.schemas.bank import BankProfile
from regintel.schemas.evidence import Evidence, EvidenceKind
from regintel.synthetic.gaps import WITHHELD_EVIDENCE
from regintel.synthetic.spec import POLICIES, ControlSpec, PolicySpec, effective_evidence

# Exact planted counts. Tests assert these.
PLANT = {
    "kyc_high_over_24m": 37,
    "kyc_high_12_24m": 275,
    "kyc_medium_8_10y": 860,
    "sanctions_after_activation": 120,
    "sanctions_not_screened": 60,
    "vendor_access_unreviewed": 17,
    "vendor_access_unreviewed_not_required": 5,
}
N_ACCOUNTS_Q = 3000
POLICY_PERIOD_DAYS = {"HIGH": 730, "MEDIUM": 3650, "LOW": 3650}  # as written in KYC-C09..C11


# ---------------- helpers ----------------
def _d(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))


def _ts(rng: random.Random, d: date) -> datetime:
    return datetime.combine(d, time(rng.randint(9, 18), rng.randint(0, 59)))


def _add_working_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _fmt(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> tuple[int, list[str]]:
    cols = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(v) for k, v in r.items()})
    return len(rows), cols


@dataclass
class GenResult:
    rows: list[dict[str, Any]]
    title: str
    period: tuple[date, date]
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    seed: int
    as_of: date
    profile: BankProfile
    _accounts: list[dict[str, Any]] | None = None

    def rng(self, name: str) -> random.Random:
        return random.Random(f"{self.seed}:{name}")  # str seeds are deterministic across runs

    @property
    def q_start(self) -> date:
        return date(self.as_of.year, 3 * ((self.as_of.month - 1) // 3) + 1, 1)

    @property
    def fy_start(self) -> date:  # Indian FY (Apr-Mar)
        y = self.as_of.year if self.as_of.month >= 4 else self.as_of.year - 1
        return date(y, 4, 1)

    def accounts(self) -> list[dict[str, Any]]:
        if self._accounts is None:
            rng, products = self.rng("accounts"), [p.id for p in self.profile.products]
            self._accounts = [
                {"account_id": f"ACC{i:06d}", "customer_id": f"NC{i:06d}",
                 "product_id": rng.choice(products),
                 "activated_at": _ts(rng, _d(rng, self.q_start, self.as_of))}
                for i in range(1, N_ACCOUNTS_Q + 1)
            ]
        return self._accounts


# ---------------- generators ----------------
def gen_kyc_periodic_updation(ctx: Ctx) -> GenResult:
    rng, dist = ctx.rng("kyc_periodic"), ctx.profile.customer_base.risk_distribution
    hi_over, hi_mid, med = PLANT["kyc_high_over_24m"], PLANT["kyc_high_12_24m"], PLANT["kyc_medium_8_10y"]
    bands = {  # (count, min_age_days, max_age_days); margins avoid boundary ambiguity
        "HIGH": [(hi_over, 740, 900), (hi_mid, 370, 725), (dist["HIGH"] - hi_over - hi_mid, 0, 360)],
        "MEDIUM": [(med, 2930, 3640), (dist["MEDIUM"] - med, 0, 2910)],
        "LOW": [(dist["LOW"], 0, 3640)],
    }
    specs = [(r, lo, hi) for r, parts in bands.items() for n, lo, hi in parts for _ in range(n)]
    rng.shuffle(specs)
    rows = []
    for i, (risk, lo, hi) in enumerate(specs, 1):
        last = ctx.as_of - timedelta(days=rng.randint(lo, hi))
        due = last + timedelta(days=POLICY_PERIOD_DAYS[risk])
        rows.append({
            "customer_id": f"CUST{i:07d}", "risk_category": risk,
            "onboarding_date": last - timedelta(days=rng.randint(0, 1500)),
            "last_kyc_update_date": last, "next_due_date_per_policy": due,
            "kyc_status": "OVERDUE" if due < ctx.as_of else "CURRENT",
        })
    high = dist["HIGH"]
    return GenResult(rows, "Customer periodic KYC updation register", (ctx.q_start, ctx.as_of), {
        "high_population": high,
        "high_overdue_vs_2y": hi_over, "high_rate_vs_2y": round(hi_over / high, 4),
        "high_overdue_vs_1y": hi_over + hi_mid, "high_rate_vs_1y": round((hi_over + hi_mid) / high, 4),
        "medium_population": dist["MEDIUM"], "medium_overdue_vs_8y_current_per_policy": med,
    })


def gen_sanctions_screening_log(ctx: Ctx) -> GenResult:
    rng, accts = ctx.rng("sanctions"), ctx.accounts()
    order = list(range(len(accts)))
    rng.shuffle(order)
    n_late, n_none = PLANT["sanctions_after_activation"], PLANT["sanctions_not_screened"]
    late, none = set(order[:n_late]), set(order[n_late:n_late + n_none])
    rows = []
    for k, a in enumerate(accts):
        act = a["activated_at"]
        if k in none:
            ts, lists, result, ok = "", "", "NOT_SCREENED", False
        elif k in late:
            ts, lists, result, ok = act + timedelta(days=rng.randint(1, 10)), "UNSC;UAPA", "CLEARED", False
        else:
            ts, lists, ok = act - timedelta(minutes=rng.randint(2, 240)), "UNSC;UAPA", True
            result = "CLEARED" if rng.random() > 0.03 else "POTENTIAL_MATCH_CLEARED"
        rows.append({"account_id": a["account_id"], "customer_id": a["customer_id"],
                     "product_id": a["product_id"], "activated_at": act, "screening_ts": ts,
                     "lists_checked": lists, "screening_result": result,
                     "screened_before_activation": ok})
    bad = n_late + n_none
    return GenResult(rows, "Sanctions screening log (onboarding)", (ctx.q_start, ctx.as_of),
                     {"population": len(accts), "exceptions": bad, "exception_rate": round(bad / len(accts), 4)})


def gen_cdd_onboarding_log(ctx: Ctx) -> GenResult:
    rng = ctx.rng("cdd")
    modes = [("Aadhaar", "AADHAAR_EKYC"), ("Aadhaar", "V-CIP"), ("Passport", "OVD_IN_PERSON"),
             ("Voter ID", "OVD_IN_PERSON"), ("Driving Licence", "OVD_IN_PERSON")]
    rows = []
    for a in ctx.accounts():
        ovd, mode = rng.choice(modes)
        rows.append({"account_id": a["account_id"], "customer_id": a["customer_id"],
                     "product_id": a["product_id"], "ovd_type": ovd, "verification_mode": mode,
                     "verification_status": "VERIFIED",
                     "verified_at": a["activated_at"] - timedelta(minutes=rng.randint(10, 600)),
                     "activated_at": a["activated_at"],
                     "risk_category": rng.choices(["LOW", "MEDIUM", "HIGH"], weights=[80, 18, 2])[0]})
    return GenResult(rows, "CDD onboarding log", (ctx.q_start, ctx.as_of),
                     {"population": len(rows), "exceptions": 0})


def gen_str_filing_log(ctx: Ctx) -> GenResult:
    rng, n = ctx.rng("str"), 120
    late_idx = rng.randrange(n)
    rows = []
    for i in range(n):
        alert = _d(rng, ctx.fy_start, ctx.as_of - timedelta(days=40))
        det = alert + timedelta(days=rng.randint(3, 25))
        wd = 8 if i == late_idx else rng.randint(1, 7)
        rows.append({"str_id": f"STR{i + 1:05d}", "alert_id": f"TMA{rng.randint(100000, 999999)}",
                     "customer_id": f"CUST{rng.randint(1, 120000):07d}", "alert_date": alert,
                     "determination_date": det, "str_filed_date": _add_working_days(det, wd),
                     "working_days_to_file": wd})
    return GenResult(rows, "STR filing log", (ctx.fy_start, ctx.as_of),
                     {"population": n, "exceptions": 1, "exception_rate": round(1 / n, 4)})


def gen_vcip_sessions(ctx: Ctx) -> GenResult:
    rng = ctx.rng("vcip")
    rows = [{"session_id": f"VCIP{i + 1:05d}", "customer_id": f"VC{i + 1:06d}",
             "session_ts": _ts(rng, _d(rng, ctx.fy_start, ctx.as_of)),
             "official_id": f"OFF{rng.randint(1, 25):03d}", "vendor_id": "V-VKY", "recorded": True,
             "geo_tagged": True, "liveness_passed": True, "account_activated": True} for i in range(800)]
    return GenResult(rows, "V-CIP session log", (ctx.fy_start, ctx.as_of), {"population": 800, "exceptions": 0})


def gen_consent_records(ctx: Ctx) -> GenResult:
    rng = ctx.rng("consent")
    purposes = ["ACCOUNT_SERVICING", "CREDIT_BUREAU_REPORTING", "MARKETING", "ANALYTICS"]
    langs = ["English", "Hindi", "Marathi", "Tamil", "Bengali"]
    rows = [{"consent_id": f"CNS{i + 1:06d}", "customer_id": a["customer_id"],
             "purpose_code": rng.choice(purposes), "notice_version": "NTC-2024.2",
             "notice_language": rng.choices(langs, weights=[60, 20, 8, 6, 6])[0],
             "captured_at": a["activated_at"] - timedelta(minutes=rng.randint(1, 30)),
             "capture_method": "AFFIRMATIVE_CHECKBOX", "status": "ACTIVE"}
            for i, a in enumerate(ctx.accounts())]
    return GenResult(rows, "Consent records", (ctx.q_start, ctx.as_of),
                     {"population": len(rows), "withdrawn_records": 0})


def gen_data_erasure_log(ctx: Ctx) -> GenResult:
    rng, rows = ctx.rng("erasure"), []
    for i in range(400):
        closed = _d(rng, date(ctx.as_of.year - 2, 6, 1), ctx.as_of - timedelta(days=200))
        lag = rng.randint(175, 185)
        rows.append({"account_id": f"CL{i + 1:06d}", "customer_id": f"XC{i + 1:06d}", "closed_on": closed,
                     "erased_on": closed + timedelta(days=lag), "days_after_closure": lag,
                     "data_categories_erased": "KYC_DOCUMENTS;CONTACT_DETAILS;TRANSACTION_HISTORY",
                     "systems": "CBS;DMS;CRM", "erasure_rule": "DPR-POL 6.2"})
    return GenResult(rows, "Personal data erasure log", (ctx.fy_start, ctx.as_of),
                     {"population": 400, "kyc_records_erased_within_5y": 400})


EXTRA_VENDORS = [  # non-material, synthetic
    ("V-PRT", "PrintHub Services (synthetic)", "Statement printing", False),
    ("V-ARC", "DataVault Archival (synthetic)", "Physical record archival", False),
    ("V-HLP", "DeskAssist (synthetic)", "IT helpdesk L1", False),
]


def gen_vendor_register(ctx: Ctx) -> GenResult:
    rng = ctx.rng("vendors")
    vendors = [(v.id, v.name, v.service, v.material) for v in ctx.profile.outsourcing_vendors]
    rows = []
    for vid, name, service, material in vendors + EXTRA_VENDORS:
        onboarded = _d(rng, date(ctx.as_of.year - 5, 1, 1), date(ctx.as_of.year - 1, 12, 31))
        rows.append({
            "vendor_id": vid, "vendor_name": name, "service": service, "material": material,
            "materiality_assessed_on": onboarded - timedelta(days=rng.randint(10, 40)),
            "due_diligence_completed_on": onboarded - timedelta(days=rng.randint(5, 30)),
            "onboarded_on": onboarded,
            "last_risk_assessment_on": (ctx.as_of - timedelta(days=rng.randint(20, 300)))
                                       if material else "",
            "contract_audit_rights_rbi": True,
            "exit_plan_documented": material or rng.random() > 0.5,
            "in_outsourcing_register": True,
        })
    return GenResult(rows, "Outsourcing register (IT service providers)", (ctx.fy_start, ctx.as_of),
                     {"population": len(rows), "material": sum(r["material"] for r in rows),
                      "exceptions": 0})


def gen_vendor_privileged_access_review(ctx: Ctx) -> GenResult:
    rng, n = ctx.rng("vendor_access"), 120
    n_un, n_un_nr = PLANT["vendor_access_unreviewed"], PLANT["vendor_access_unreviewed_not_required"]
    material = [v.id for v in ctx.profile.outsourcing_vendors if v.material]
    idx = list(range(n))
    rng.shuffle(idx)
    unreviewed, unreviewed_not_req = set(idx[:n_un]), set(idx[:n_un_nr])  # subset by construction
    rows = []
    for i in range(n):
        reviewed = i not in unreviewed
        required = (rng.random() > 0.06) if reviewed else (i not in unreviewed_not_req)
        rows.append({
            "account_id": f"SPA{i + 1:04d}", "vendor_id": rng.choice(material),
            "system": rng.choice(["CBS", "CBS-DB", "V-CIP platform", "DR site", "Middleware"]),
            "access_level": rng.choice(["DBA", "SYSADMIN", "APP_ADMIN"]),
            "granted_on": _d(rng, date(ctx.as_of.year - 3, 1, 1), ctx.q_start - timedelta(days=1)),
            "still_required": required, "reviewed_in_quarter": reviewed,
            "review_date": _d(rng, ctx.q_start, ctx.as_of) if reviewed else "",
            "reviewer": f"OWN{rng.randint(1, 12):02d}" if reviewed else "",
            "action": ("RETAINED" if required else "REVOKED") if reviewed else "",
        })
    return GenResult(rows, "Service-provider privileged access review (quarterly)",
                     (ctx.q_start, ctx.as_of),
                     {"population": n, "exceptions": n_un, "exception_rate": round(n_un / n, 4),
                      "unreviewed_not_required": n_un_nr})


def gen_access_reviews(ctx: Ctx) -> GenResult:
    rng, n = ctx.rng("access_reviews"), 300
    rows = [{
        "account_id": f"PRV{i + 1:04d}",
        "system": rng.choice(["CBS", "AD", "SIEM", "Treasury", "Payments switch", "CRM"]),
        "account_type": rng.choice(["ADMIN", "DBA", "SERVICE"]),
        "owner": f"EMP{rng.randint(1000, 9999)}", "reviewed_in_quarter": True,
        "review_date": _d(rng, ctx.q_start, ctx.as_of), "reviewer": f"SYSOWN{rng.randint(1, 20):02d}",
        "action": rng.choices(["RETAINED", "REVOKED", "MODIFIED"], weights=[90, 6, 4])[0],
    } for i in range(n)]
    return GenResult(rows, "Internal privileged access review (quarterly)", (ctx.q_start, ctx.as_of),
                     {"population": n, "exceptions": 0})


def gen_security_incidents(ctx: Ctx) -> GenResult:
    rng, n, n_pd = ctx.rng("incidents"), 24, 4
    pd_idx = set(rng.sample(range(n), n_pd))
    rows = []
    for i in range(n):
        pd_inv = i in pd_idx
        detected = _ts(rng, _d(rng, ctx.fy_start, ctx.as_of))
        hours = round(rng.uniform(0.5, 5.8), 1)
        rows.append({
            "incident_id": f"INC{i + 1:04d}", "detected_at": detected,
            "category": "DATA_LEAK" if pd_inv else rng.choice(
                ["PHISHING", "MALWARE", "DDOS", "UNAUTHORISED_ACCESS"]),
            "severity": "HIGH" if pd_inv else rng.choice(["LOW", "MEDIUM", "HIGH"]),
            "personal_data_involved": pd_inv,
            "records_affected": rng.randint(200, 15000) if pd_inv else 0,
            "certin_reported_at": detected + timedelta(hours=hours), "hours_to_certin": hours,
            "rbi_reported": True, "notified_to": "CERT-In;RBI",
        })
    return GenResult(rows, "Cyber security incident register", (ctx.fy_start, ctx.as_of),
                     {"population": n, "personal_data_incidents": n_pd,
                      "dpb_or_principal_notified": 0})


GENERATORS: dict[str, Callable[[Ctx], GenResult]] = {
    "kyc_periodic_updation": gen_kyc_periodic_updation,
    "sanctions_screening_log": gen_sanctions_screening_log,
    "cdd_onboarding_log": gen_cdd_onboarding_log,
    "str_filing_log": gen_str_filing_log,
    "vcip_sessions": gen_vcip_sessions,
    "consent_records": gen_consent_records,
    "data_erasure_log": gen_data_erasure_log,
    "vendor_register": gen_vendor_register,
    "vendor_privileged_access_review": gen_vendor_privileged_access_review,
    "access_reviews": gen_access_reviews,
    "security_incidents": gen_security_incidents,
}


# ---------------- attestations + manifest ----------------
def _attestation_md(ctx: Ctx, p: PolicySpec, c: ControlSpec) -> str:
    last = c.last_reviewed or p.last_reviewed
    return (
        f"# Control Attestation: {c.id} {c.name}\n\n"
        f"- Policy: {p.id} v{p.version}, clause {c.clause_no}\n"
        f"- Control owner: {c.owner_role}\n"
        f"- Attestation period: {ctx.fy_start} to {ctx.as_of}\n"
        f"- Last design review of this control: {last}\n"
        f"- Attested on: {ctx.as_of}\n\n"
        "The control owner confirms that the control below operated during the attestation period "
        "as documented, and that no exceptions were reported to the owner's knowledge.\n\n"
        f"> {c.text}\n\n"
        f"Signed: {c.owner_role} (SYNTHETIC)\n"
    )


def _ev_id(name: str) -> str:
    return "EV-" + name.upper().replace("_", "-")


def generate_evidence(ctx: Ctx, ev_dir: Path) -> tuple[list[Evidence], dict[str, dict[str, Any]]]:
    """Writes CSVs, attestations and manifest.json. Returns (manifest, ground-truth stats)."""
    key_to_controls: dict[str, list[str]] = defaultdict(list)
    ctl_lookup: dict[str, tuple[PolicySpec, ControlSpec]] = {}
    for p in POLICIES:
        for c in p.controls():
            ctl_lookup[c.id] = (p, c)
            for k in effective_evidence(c):
                key_to_controls[k].append(c.id)

    recorded = datetime.combine(ctx.as_of, time(), tzinfo=timezone.utc)  # fixed -> deterministic
    manifest: list[Evidence] = []
    stats: dict[str, dict[str, Any]] = {}

    for name, fn in GENERATORS.items():
        res = fn(ctx)
        n, cols = _write_csv(ev_dir / f"{name}.csv", res.rows)
        stats[name] = {"rows": n, **res.stats}
        manifest.append(Evidence(
            id=_ev_id(name), control_ids=key_to_controls.get(f"csv:{name}", []), title=res.title,
            kind=EvidenceKind.REPORT if "register" in name else EvidenceKind.LOG,
            path=f"evidence/{name}.csv", period_start=res.period[0], period_end=res.period[1],
            collected_on=ctx.as_of, row_count=n, columns=cols, recorded_at=recorded,
        ))

    att_dir = ev_dir / "attestations"
    att_dir.mkdir(parents=True, exist_ok=True)
    for key, ctl_ids in key_to_controls.items():
        if not key.startswith("att:") or key in WITHHELD_EVIDENCE:
            continue  # withheld evidence => planted MISSING_EVIDENCE gaps
        name = key[4:]
        p, c = ctl_lookup[ctl_ids[0]]
        (att_dir / f"{name}.md").write_text(_attestation_md(ctx, p, c), encoding="utf-8")
        manifest.append(Evidence(
            id=_ev_id(name), control_ids=ctl_ids, title=f"Control owner attestation - {c.id}",
            kind=EvidenceKind.ATTESTATION, path=f"evidence/attestations/{name}.md",
            period_start=ctx.fy_start, period_end=ctx.as_of, collected_on=ctx.as_of,
            recorded_at=recorded,
        ))

    manifest.sort(key=lambda e: e.id)
    (ev_dir / "manifest.json").write_text(
        json.dumps([e.model_dump(mode="json") for e in manifest], indent=2), encoding="utf-8")
    return manifest, stats