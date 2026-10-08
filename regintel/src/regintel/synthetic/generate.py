"""Generate the synthetic bank.  Usage:  python -m regintel.synthetic.generate [--seed 42]"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from regintel.config import get_bank_profile
from regintel.settings import get_settings
from regintel.synthetic.answer_key import build_answer_key
from regintel.synthetic.evidence_gen import Ctx, generate_evidence
from regintel.synthetic.policy_writer import write_policies

DEFAULT_AS_OF = date(2025, 3, 31)  # Indian FY end; fixed for reproducibility


def generate_all(root: Path, seed: int = 42, as_of: date = DEFAULT_AS_OF,
                 clean: bool = True) -> dict[str, Any]:
    pol, ev, gold = root / "policies", root / "evidence", root / "golden"
    if clean:  # golden/ is NOT wiped: it will also hold hand-labelled eval sets later
        for d in (pol, ev):
            shutil.rmtree(d, ignore_errors=True)
    for d in (pol, ev, gold):
        d.mkdir(parents=True, exist_ok=True)

    write_policies(pol)
    manifest, stats = generate_evidence(Ctx(seed=seed, as_of=as_of, profile=get_bank_profile()), ev)
    key = build_answer_key(seed, as_of, stats, manifest)
    (gold / "answer_key.json").write_text(json.dumps(key, indent=2, default=str), encoding="utf-8")
    return key


app = typer.Typer(add_completion=False)


@app.command()
def main(seed: int = 42, as_of: str = DEFAULT_AS_OF.isoformat(),
         out: Optional[Path] = None, clean: bool = True) -> None:  # noqa: UP007
    root = out or get_settings().data_dir
    key = generate_all(root, seed, date.fromisoformat(as_of), clean)
    con = Console()

    t = Table(title=f"Bharat Unity Bank (synthetic) -> {root}")
    t.add_column("Artifact"); t.add_column("Count", justify="right")
    for k, v in key["summary"].items():
        t.add_row(k.replace("_", " "), str(v))
    con.print(t)

    g = Table(title="Planted gaps (ground truth)")
    for col in ("ID", "Type", "Control", "Title"):
        g.add_column(col)
    for gap in key["planted_gaps"]:
        g.add_row(gap["id"], gap["gap_type"], gap["control_id"] or "-", gap["title"])
    con.print(g)

    cd = key["change_demo"]
    con.print(f"[bold]Change demo[/] {cd['control_id']}: baseline {cd['baseline']['overdue']}/"
              f"{cd['population']} ({cd['baseline']['rate']:.1%}) -> hypothetical "
              f"{cd['hypothetical']['overdue']}/{cd['population']} ({cd['hypothetical']['rate']:.1%})")


if __name__ == "__main__":
    app()