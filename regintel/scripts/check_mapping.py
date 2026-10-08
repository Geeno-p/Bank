"""Phase 3B check vs answer key:  python scripts/check_mapping.py"""
import json
import sys
from collections import Counter, defaultdict

from rich.console import Console
from rich.table import Table

from regintel.agents.extraction import load_extracted
from regintel.evals.match import best_match
from regintel.schemas.mapping import ControlMapping, ObligationCoverage
from regintel.schemas.obligation import Obligation
from regintel.settings import get_settings

con = Console()
obls, maps = load_extracted("obligations", Obligation), load_extracted("mappings", ControlMapping)
cov = {c.obligation_id: c for c in load_extracted("coverage", ObligationCoverage)}
if not maps:
    sys.exit("Run: python -m regintel.agents.mapping")
key = json.loads((get_settings().data_dir / "golden" / "answer_key.json").read_text(encoding="utf-8"))
by_reg = defaultdict(list)
for o in obls:
    by_reg[o.regulation_id].append(o)
pair = {(m.obligation_id, m.control_id): m for m in maps}

con.print(f"[bold]Obligation coverage:[/] {dict(Counter(c.coverage.value for c in cov.values()))}  |  "
          f"mappings {len(maps)} {dict(Counter(m.coverage.value for m in maps))}")

# 1) recall of expected obligation->control links
miss = Table(title="Expected links NOT mapped")
for col in ("Control", "Hint", "Obligation", "Mapped instead"):
    miss.add_column(col)
hits = total = 0
for c in key["controls"]:
    for lk in c["expected_links"]:
        o, s = best_match(lk["hint"], by_reg.get(lk["regulation_id"], []))
        if not o or s < 0.5:
            continue
        total += 1
        if (o.id, c["control_id"]) in pair:
            hits += 1
        else:
            miss.add_row(c["control_id"], lk["hint"][:45], o.source.clause_ref,
                         ", ".join(cov[o.id].control_ids) if o.id in cov else "-")
con.print(f"[bold]Expected-link recall:[/] {hits}/{total} = {hits / max(total, 1):.0%}")
if miss.row_count:
    con.print(miss)

# 2) planted gaps visible at the mapping stage
EXPECT = {"G01": "PARTIAL", "G05": "PARTIAL", "G02": "NONE", "G06": "NONE"}
t = Table(title="Planted gaps at mapping stage")
for col in ("Gap", "Expected", "Got", "Detail"):
    t.add_column(col)
for g in key["planted_gaps"]:
    if g["id"] not in EXPECT:
        continue
    o, s = best_match(g["obligation_hint"], by_reg.get(g["regulation_id"], []))
    if not o or s < 0.5:
        t.add_row(g["id"], EXPECT[g["id"]], "[yellow]obligation not found[/]", "")
        continue
    if g["control_id"]:
        m = pair.get((o.id, g["control_id"]))
        got, detail = (m.coverage.value, "; ".join(m.missing_elements)[:90]) if m else ("NOT MAPPED", "")
    else:
        c = cov.get(o.id)
        got, detail = (c.coverage.value, f"mapped to {c.control_ids}" if c.control_ids else "") if c else ("?", "")
    mark = "[green]✓" if got == EXPECT[g["id"]] else "[red]✗"
    t.add_row(g["id"], EXPECT[g["id"]], f"{mark} {got}[/]", detail)
con.print(t)