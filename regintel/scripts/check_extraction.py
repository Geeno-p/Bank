"""Phase 2B check vs ground truth:  python scripts/check_extraction.py"""
import json
import re
from collections import Counter

from rich.console import Console
from rich.table import Table

from regintel.agents.extraction import load_extracted
from regintel.schemas.control import Control
from regintel.schemas.obligation import Obligation
from regintel.settings import get_settings
from regintel.synthetic.spec import all_controls

con = Console()
STOP = {"the", "and", "for", "with", "any", "its", "their", "when", "not", "than", "after", "before",
        "from", "under", "into", "are", "least", "each", "who", "all", "shall", "such"}
PLANTED_PARAMS = {"KYC-C09": {"2", "two"}, "KYC-C10": {"10", "ten"}, "DPR-C09": {"180"}, "ISB-C08": {"6", "six"}}


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in STOP}


def check_controls() -> None:
    spec = {c.id: c for c in all_controls()}
    got = {c.id: c for c in load_extracted("controls", Control)}
    found = spec.keys() & got.keys()
    con.print(f"[bold]Controls found:[/] {len(found)}/{len(spec)}  missing: {sorted(spec.keys() - got.keys()) or '-'}")
    for field in ("control_type", "nature", "frequency"):
        ok = sum(getattr(got[i], field) == getattr(spec[i], field) for i in found)
        con.print(f"  {field:13} accuracy: {ok}/{len(found)}")
    for cid, vals in PLANTED_PARAMS.items():
        ps = got[cid].parameters if cid in got else []
        hit = any(p.value.split()[0].lower() in vals for p in ps if p.value.strip())
        shown = [f"{p.name}={p.value}{p.unit or ''}" for p in ps]
        con.print(f"  planted parameter {cid}: {'[green]✓' if hit else '[red]✗'}[/] {shown}")


def check_obligations() -> None:
    obls = load_extracted("obligations", Obligation)
    if not obls:
        con.print("[yellow]No obligations yet: ingest regulation PDFs, then run extraction.[/]")
        return
    con.print(f"[bold]Obligations:[/] {len(obls)}  {dict(Counter(o.regulation_id for o in obls))}")
    con.print(f"  modality {dict(Counter(o.modality.value for o in obls))}")
    key = json.loads((get_settings().data_dir / "golden" / "answer_key.json").read_text(encoding="utf-8"))
    hints = {(g["regulation_id"], g["obligation_hint"]) for g in key["planted_gaps"]}
    hints |= {(lk["regulation_id"], lk["hint"]) for c in key["controls"] for lk in c["expected_links"]}
    present = {o.regulation_id for o in obls}
    t = Table(title="Indicative recall of obligations the demo needs (keyword overlap)")
    for col in ("Reg", "Hint", "Best match", "Score"):
        t.add_column(col)
    hits = n = 0
    for reg, hint in sorted(hints):
        if reg not in present:
            continue
        hw, n = words(hint), n + 1
        best = max((o for o in obls if o.regulation_id == reg),
                   key=lambda o: len(hw & words(o.action + " " + o.source.quote)))
        score = len(hw & words(best.action + " " + best.source.quote)) / max(len(hw), 1)
        hits += score >= 0.5
        t.add_row(reg, hint[:55], f"{best.source.clause_ref}: {best.action[:45]}", f"{score:.0%}")
    con.print(t)
    con.print(f"  indicative recall: {hits}/{n} (score >= 50%)")


if __name__ == "__main__":
    check_controls()
    check_obligations()