"""Phase 3A check: applicability summary + candidate recall@k.  python scripts/check_retrieval.py [k]"""
import json
import sys
from collections import Counter, defaultdict

from rich.console import Console
from rich.table import Table

from regintel.agents.extraction import load_extracted
from regintel.evals.match import best_match
from regintel.retrieval.hybrid import ControlIndex
from regintel.schemas.control import Control
from regintel.schemas.obligation import ApplicabilityDecision, Obligation
from regintel.settings import get_settings

K = int(sys.argv[1]) if len(sys.argv) > 1 else 6
con = Console()
obls, ctls = load_extracted("obligations", Obligation), load_extracted("controls", Control)
if not obls or not ctls:
    sys.exit("Run Phase 2B extraction first (controls + obligations).")

decs = load_extracted("applicability", ApplicabilityDecision)
if decs:
    con.print(f"[bold]Applicability:[/] {sum(d.applicable for d in decs)}/{len(decs)} applicable | "
              f"{dict(Counter(d.decided_by for d in decs))} | review {sum(d.confidence < 0.8 for d in decs)}")

key = json.loads((get_settings().data_dir / "golden" / "answer_key.json").read_text(encoding="utf-8"))
idx = ControlIndex(ctls)
by_reg = defaultdict(list)
for o in obls:
    by_reg[o.regulation_id].append(o)

misses = Table(title=f"Expected control NOT in top-{K}")
for col in ("Control", "Hint", "Matched obligation", "Top-3 retrieved"):
    misses.add_column(col)
hits = total = unmatched = 0
for c in key["controls"]:
    for lk in c["expected_links"]:
        if lk["regulation_id"] not in by_reg:
            continue
        o, score = best_match(lk["hint"], by_reg[lk["regulation_id"]])
        if score < 0.5:
            unmatched += 1
            continue
        total += 1
        ids = [x.control.id for x in idx.search(o, k=K)]
        if c["control_id"] in ids:
            hits += 1
        else:
            misses.add_row(c["control_id"], lk["hint"][:45], f"{o.source.clause_ref}: {o.action[:35]}", ", ".join(ids[:3]))

con.print(f"[bold]Candidate recall@{K}:[/] {hits}/{total} = {hits / max(total, 1):.0%}   "
          f"(links whose obligation was not found in the PDFs: {unmatched})")
if misses.row_count:
    con.print(misses)

for g in key["planted_gaps"]:
    if g["gap_type"] == "UNMAPPED" and g["regulation_id"] in by_reg:
        o, s = best_match(g["obligation_hint"], by_reg[g["regulation_id"]])
        if o and s >= 0.5:
            top = [x.control.id for x in idx.search(o, k=3)]
            con.print(f"{g['id']} (3B should judge NONE): {o.source.clause_ref} -> nearest {top}")
        else:
            con.print(f"[yellow]{g['id']}: obligation not found in {g['regulation_id']} extraction[/]")