"""Phase 4 check vs answer key + change-demo preview:  python scripts/check_gaps.py"""
import json
import sys
from collections import Counter, defaultdict

from rich.console import Console
from rich.table import Table

from regintel.agents.extraction import load_extracted
from regintel.engines.evidence_tests import as_of_from, load_manifest, run_tests
from regintel.evals.match import best_match
from regintel.schemas.control import Control
from regintel.schemas.gap import Gap
from regintel.schemas.obligation import Obligation
from regintel.settings import get_settings

con = Console()
gaps = load_extracted("gaps", Gap)
if not gaps:
    sys.exit("Run: python -m regintel.agents.gaps")
key = json.loads((get_settings().data_dir / "golden" / "answer_key.json").read_text(encoding="utf-8"))
by_reg = defaultdict(list)
for o in load_extracted("obligations", Obligation):
    by_reg[o.regulation_id].append(o)
con.print(f"[bold]Gaps:[/] {len(gaps)} | {dict(Counter(g.gap_type.value for g in gaps))} | "
          f"{dict(Counter(g.risk.band.value for g in gaps))}")

t = Table(title="Planted gaps")
for col in ("Gap", "Expected", "Control", "Found", "Band", "Detail"):
    t.add_column(col)
found, matched = 0, set()
for pg in key["planted_gaps"]:
    if pg["control_id"]:
        hits = [g for g in gaps if g.control_id == pg["control_id"] and g.gap_type.value == pg["gap_type"]]
        other = sorted({g.gap_type.value for g in gaps if g.control_id == pg["control_id"]})
    else:
        o, s = best_match(pg["obligation_hint"], by_reg.get(pg["regulation_id"], []))
        hits = [g for g in gaps if o and s >= 0.5 and g.obligation_id == o.id and g.gap_type.value == "UNMAPPED"]
        other = []
    g = hits[0] if hits else None
    found += bool(g)
    matched |= {h.id for h in hits}
    t.add_row(pg["id"], pg["gap_type"], pg["control_id"] or "-",
              "[green]✓[/]" if g else f"[red]✗[/] {other or ''}", g.risk.band.value if g else "",
              g.description[:70] if g else "")
con.print(t)
con.print(f"[bold]Planted-gap recall:[/] {found}/{len(key['planted_gaps'])}")
clean = set(key["false_positive_traps"]["clean_controls"])
fp = {g.id for g in gaps if g.control_id in clean}
con.print(f"Gaps on clean controls (false positives): {len(fp)} "
          f"{sorted({g.control_id for g in gaps if g.id in fp})}")
con.print(f"Other unplanned gaps (review; may be legitimate): {len([g for g in gaps if g.id not in matched | fp])}")

ctls, man = load_extracted("controls", Control), load_manifest()
for label, ov in (("baseline (policy 2y)", {}), ("hypothetical 1y rule", {"KYC-C09": 365})):
    r = next((r for _, r in run_tests(ctls, man, as_of_from(man), "CHECK", ov) if r.control_id == "KYC-C09"), None)
    if r:
        con.print(f"Change demo KYC-C09 {label}: {r.exceptions}/{r.population} ({r.exception_rate:.1%}) "
                  f"-> {'PASS' if r.passed else 'FAIL'}")