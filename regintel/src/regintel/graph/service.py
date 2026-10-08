"""One entry point to start, resume and inspect workflow runs (used by the CLI, scheduler and UI)."""
from __future__ import annotations

import json
import uuid
from typing import Any

from langgraph.types import Command

from regintel.db import get_conn, init_db
from regintel.graph.flow import build, sqlite_checkpointer
from regintel.schemas.common import utcnow
from regintel.settings import get_settings 

def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


def _app():
    return build(sqlite_checkpointer())


def snapshot(thread: str, app=None) -> dict[str, Any]:
    app = app or _app()
    s = app.get_state(_cfg(thread))
    review = s.tasks[0].interrupts[0].value if s.next and s.tasks and s.tasks[0].interrupts else {}
    return {"run_id": thread, "status": "PAUSED" if s.next else s.values.get("status"),
            "trace": s.values.get("trace", []), "review": review, "values": s.values}


def _invoke(inp: Any, thread: str) -> dict[str, Any]:
    app = _app()
    try:
        app.invoke(inp, _cfg(thread))
    except Exception as e:
        with get_conn() as c:
            c.execute("UPDATE runs SET status='FAILED', finished_at=?, summary=? WHERE run_id=?",
                      (utcnow().isoformat(), json.dumps({"error": str(e)[:500]}), thread))
        raise
    snap = snapshot(thread, app)
    if snap["status"] == "PAUSED":
        with get_conn() as c:
            c.execute("UPDATE runs SET status='PAUSED' WHERE run_id=?", (thread,))
    return snap


def start(event_type: str, payload: dict | None = None, auto_approve: bool = False,
          no_kg: bool | None = None) -> dict[str, Any]:
    if no_kg is None:
        no_kg = not get_settings().kg_write_default
    init_db()
    run_id = f"RUN-{uuid.uuid4().hex[:10]}"
    return _invoke({"run_id": run_id, "event_type": event_type, "payload": payload or {},
                    "auto_approve": auto_approve, "no_kg": no_kg, "trace": []}, run_id)


def resume(thread: str, action: str = "approve", reviewer: str = "compliance.officer",
           notes: str = "") -> dict[str, Any]:
    init_db()
    return _invoke(Command(resume={"action": action, "reviewer": reviewer, "notes": notes}), thread)


def _rows(sql: str, *args) -> list[dict]:
    init_db()
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def paused_runs() -> list[dict]:
    return _rows("SELECT run_id, event_type, started_at FROM runs WHERE status='PAUSED' ORDER BY started_at DESC")


def recent_runs(n: int = 20) -> list[dict]:
    return _rows("SELECT run_id, event_type, status, started_at, finished_at FROM runs "
                 "ORDER BY started_at DESC LIMIT ?", n)