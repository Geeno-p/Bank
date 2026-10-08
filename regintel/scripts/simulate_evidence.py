"""Simulate new evidence arriving, for the monitoring demo.
  python scripts/simulate_evidence.py fix-vendor-access   # remediates G10 (ITO-C06 should pass)
  python scripts/simulate_evidence.py late-str            # 12 STRs filed late (new gap on KYC-C12)
  python scripts/simulate_evidence.py reset               # regenerate the original evidence"""
import sys
from datetime import date

import pandas as pd

from regintel.config import get_bank_profile
from regintel.settings import get_settings
from regintel.synthetic.evidence_gen import Ctx, _add_working_days, generate_evidence

EV = get_settings().data_dir / "evidence"


def fix_vendor_access() -> None:
    p = EV / "vendor_privileged_access_review.csv"
    df = pd.read_csv(p, keep_default_na=False)
    m = df["reviewed_in_quarter"].astype(str).str.lower() != "true"
    df.loc[m, ["reviewed_in_quarter", "review_date", "reviewer"]] = [True, "2025-03-28", "OWN01"]
    req = df.loc[m, "still_required"].astype(str).str.lower() == "true"
    df.loc[m, "action"] = req.map({True: "RETAINED", False: "REVOKED"})
    df.to_csv(p, index=False)
    print(f"remediated {int(m.sum())} vendor accounts -> ITO-C06 should now pass (G10 closes)")


def late_str(n: int = 12) -> None:
    p = EV / "str_filing_log.csv"
    df = pd.read_csv(p)
    idx = df.index[df["working_days_to_file"] <= 7][:n]
    df.loc[idx, "working_days_to_file"] = 11
    df.loc[idx, "str_filed_date"] = [_add_working_days(date.fromisoformat(d), 11).isoformat()
                                     for d in df.loc[idx, "determination_date"]]
    df.to_csv(p, index=False)
    print(f"{len(idx)} STRs now filed late -> KYC-C12 should fail")


def reset() -> None:
    generate_evidence(Ctx(seed=42, as_of=date(2025, 3, 31), profile=get_bank_profile()), EV)
    print("evidence regenerated (original planted state)")


if __name__ == "__main__":
    {"fix-vendor-access": fix_vendor_access, "late-str": late_str, "reset": reset}[sys.argv[1]]()