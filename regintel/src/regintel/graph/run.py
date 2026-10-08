"""Run the agentic workflow.
  python -m regintel.graph.run new-version --doc RBI-KYC-MD --version v-amended [--auto-approve]
  python -m regintel.graph.run resume <THREAD> --action approve|reject --reviewer NAME
  python -m regintel.graph.run evidence
  python -m regintel.graph.run diagram"""
from __future__ import annotations

import uuid

import typer
from langgraph.types import Command
from rich.console import Console
from rich.table import Table

from regintel.db import init_db
from regintel.graph.flow import build, sqlite_checkpointer

cli, con = typer.Typer(add_completion=False), Console()


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


def _report(app, thread: str) -> None:
    snap = app.get_state(_cfg(thread))
    t = Table(title=f"Agent trace {thread}")
    for col in ("Node", "ms", "Info"):
        t.add_column(col)
    for x in snap.values.get("trace", []):
        t.add_row(x["node"], str(x["ms"]), str(x["info"])[:110])
    con.print(t)
    if snap.next:
        payload = snap.tasks[0].interrupts[0].value if snap.tasks and snap.tasks[0].interrupts else {}
        con.print("[bold yellow]⏸ Paused for human review[/]")
        for r in payload.get("review", []):
            con.print(f"  [bold]{r['band']}[/] {r['gap_id']}: {r['title']}\n    {r['detail']}")
        for i in payload.get("issues", []):
            con.print(f"  [red]issue[/] {i}")
        con.print(f"Resume: python -m regintel.graph.run resume {thread} --action approve --reviewer <name>")
    else:
        con.print(f"[bold green]Status:[/] {snap.values.get('status')}")


def _start(event_type: str, payload: dict, auto_approve: bool, no_kg: bool) -> None:
    init_db()
    run_id = f"RUN-{uuid.uuid4().hex[:10]}"
    app = build(sqlite_checkpointer())
    app.invoke({"run_id": run_id, "event_type": event_type, "payload": payload,
                "auto_approve": auto_approve, "no_kg": no_kg, "trace": []}, _cfg(run_id))
    _report(app, run_id)


@cli.command("new-version")
def new_version(doc: str = "RBI-KYC-MD", version: str = "v-amended",
                auto_approve: bool = False, no_kg: bool = False) -> None:
    _start("NEW_VERSION", {"doc_id": doc, "version": version}, auto_approve, no_kg)


@cli.command()
def evidence(auto_approve: bool = False, no_kg: bool = False) -> None:
    _start("EVIDENCE_UPLOADED", {}, auto_approve, no_kg)


@cli.command()
def resume(thread: str, action: str = "approve", reviewer: str = "compliance.officer", notes: str = "") -> None:
    init_db()
    app = build(sqlite_checkpointer())
    app.invoke(Command(resume={"action": action, "reviewer": reviewer, "notes": notes}), _cfg(thread))
    _report(app, thread)


@cli.command()
def diagram() -> None:
    print(build().get_graph().draw_mermaid())


if __name__ == "__main__":
    cli()