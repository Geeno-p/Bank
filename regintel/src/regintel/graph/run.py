"""CLI for the agentic workflow.
  python -m regintel.graph.run new-version --doc RBI-KYC-MD --version v-amended [--auto-approve]
  python -m regintel.graph.run evidence | paused | diagram
  python -m regintel.graph.run resume <RUN> --action approve|reject --reviewer NAME"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from regintel.graph import service
from regintel.graph.flow import build

cli, con = typer.Typer(add_completion=False), Console()


def show(snap: dict) -> None:
    t = Table(title=f"Agent trace {snap['run_id']}")
    for col in ("Node", "ms", "Info"):
        t.add_column(col)
    for x in snap["trace"]:
        t.add_row(x["node"], str(x["ms"]), str(x["info"])[:110])
    con.print(t)
    if snap["status"] == "PAUSED":
        con.print("[bold yellow]⏸ Paused for human review[/]")
        for r in snap["review"].get("review", []):
            con.print(f"  [bold]{r['band']}[/] {r['gap_id']}: {r['title']}\n    {r['detail']}")
        for i in snap["review"].get("issues", []):
            con.print(f"  [red]issue[/] {i}")
        con.print(f"Resume: python -m regintel.graph.run resume {snap['run_id']} --action approve --reviewer <name>")
    else:
        con.print(f"[bold green]Status:[/] {snap['status']}")


@cli.command("new-version")
def new_version(doc: str = "RBI-KYC-MD", version: str = "v-amended",
                auto_approve: bool = False, no_kg: bool = False) -> None:
    show(service.start("NEW_VERSION", {"doc_id": doc, "version": version}, auto_approve, no_kg))


@cli.command()
def evidence(auto_approve: bool = False, no_kg: bool = False) -> None:
    show(service.start("EVIDENCE_UPLOADED", {}, auto_approve, no_kg))


@cli.command()
def resume(thread: str, action: str = "approve", reviewer: str = "compliance.officer", notes: str = "") -> None:
    show(service.resume(thread, action, reviewer, notes))


@cli.command()
def paused() -> None:
    for r in service.paused_runs() or [{"run_id": "none", "event_type": "", "started_at": ""}]:
        con.print(f"{r['run_id']}  {r['event_type']}  {r['started_at']}")


@cli.command()
def diagram() -> None:
    print(build().get_graph().draw_mermaid())


if __name__ == "__main__":
    cli()