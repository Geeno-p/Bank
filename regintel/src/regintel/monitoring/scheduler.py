"""Continuous monitoring: regulation watch, evidence watch, scheduled re-tests, KRI tracking.
  python -m regintel.monitoring.scheduler            # run continuously (Ctrl+C to stop)
  python -m regintel.monitoring.scheduler --once     # run every job once, then exit"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path

import typer
import yaml
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from rich.console import Console

from regintel.agents.extraction import load_extracted
from regintel.config import get_sources
from regintel.db import audit, get_conn, init_db
from regintel.graph import service
from regintel.ingestion.run import out_dir
from regintel.schemas.common import utcnow
from regintel.schemas.evidence import ControlTestResult
from regintel.schemas.gap import Gap
from regintel.settings import get_settings

con, log = Console(), logging.getLogger("regintel.monitor")


def cfg() -> dict:
    return yaml.safe_load((get_settings().config_dir / "monitoring.yaml").read_text(encoding="utf-8"))


def _state(key: str, value: str | None = None) -> str | None:
    with get_conn() as c:
        if value is None:
            r = c.execute("SELECT value FROM monitor_state WHERE key=?", (key,)).fetchone()
            return r["value"] if r else None
        c.execute("INSERT OR REPLACE INTO monitor_state VALUES (?, ?, ?)", (key, value, utcnow().isoformat()))
    return value


def _run_line(job: str, res: dict) -> str:
    flag = "  ⏸ needs review" if res["status"] == "PAUSED" else ""
    return f"{job}: {res['run_id']} -> {res['status']}{flag}"


# ---------- jobs (each returns lines to print) ----------
def regulation_watch() -> list[str]:
    root, out = get_settings().root, []
    for reg in get_sources().regulations:
        versions = sorted(reg.versions, key=lambda v: v.effective_date or date.min)
        for v in versions[1:]:                       # base versions are loaded by the bulk pipeline
            p = root / v.path
            if not p.exists():
                continue
            meta = out_dir(reg.id, v.label) / "ingest_meta.json"
            known = json.loads(meta.read_text(encoding="utf-8")).get("sha256") if meta.exists() else None
            if known == hashlib.sha256(p.read_bytes()).hexdigest():
                continue
            audit("monitor:regulation_watch", "DETECTED", "doc_version", f"{reg.id}/{v.label}", {"path": v.path})
            out.append(_run_line("regulation_watch",
                                 service.start("NEW_VERSION", {"doc_id": reg.id, "version": v.label, "source": "monitor"})))
    return out


def _evidence_digest() -> str:
    h = hashlib.sha256()
    for p in sorted((get_settings().data_dir / "evidence").rglob("*")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def evidence_watch() -> list[str]:
    d, prev = _evidence_digest(), _state("evidence_digest")
    if prev is None:                                 # first run: record a baseline, don't trigger
        _state("evidence_digest", d)
        return ["evidence_watch: baseline recorded"]
    if prev == d:
        return []
    audit("monitor:evidence_watch", "DETECTED", "evidence", "repository", {})
    res = service.start("EVIDENCE_UPLOADED", {"source": "monitor"})
    _state("evidence_digest", d)                     # only after a successful run, so failures retry
    return [_run_line("evidence_watch", res)]


def scheduled_test() -> list[str]:
    return [_run_line("scheduled_test", service.start("SCHEDULED_TEST", {"source": "monitor"}))]


def kri_value(k: dict, results: list[ControlTestResult], gaps: list[Gap]) -> float | None:
    if k["metric"] == "exception_rate":
        r = next((r for r in results if r.control_id == k["control_id"]), None)
        return r.exception_rate if r else None
    if k["metric"] == "open_gaps":
        return float(sum(1 for g in gaps if g.risk and g.risk.band.value in k["bands"]))
    raise ValueError(f"unknown KRI metric {k['metric']}")


def kri_check() -> list[str]:
    results, gaps = load_extracted("test_results", ControlTestResult), load_extracted("gaps", Gap)
    lines, transitions = [], []
    with get_conn() as c:
        for k in cfg()["kris"]:
            v = kri_value(k, results, gaps)
            if v is None:
                continue
            status = "BREACH" if v > k["threshold"] else "OK"
            last = c.execute("SELECT status FROM kri_snapshots WHERE kri_id=? ORDER BY id DESC LIMIT 1",
                             (k["id"],)).fetchone()
            c.execute("INSERT INTO kri_snapshots (ts, kri_id, value, threshold, status) VALUES (?, ?, ?, ?, ?)",
                      (utcnow().isoformat(), k["id"], v, k["threshold"], status))
            if (last["status"] if last else "OK") != status:
                transitions.append((k, v, status))
            colour = "red" if status == "BREACH" else "green"
            lines.append(f"kri_check: {k['id']} {k['name']}: {v:.3g} (limit {k['threshold']:g}) [{colour}]{status}[/]")
    for k, v, status in transitions:                 # audit after the connection is closed (no lock clash)
        audit("monitor:kri", "BREACH" if status == "BREACH" else "RECOVERED", "kri", k["id"],
              {"name": k["name"], "value": v, "threshold": k["threshold"]})
    return lines


JOBS = {"regulation_watch": regulation_watch, "evidence_watch": evidence_watch,
        "scheduled_test": scheduled_test, "kri_check": kri_check}


def _safe(name: str):
    def run() -> None:
        try:
            lines = JOBS[name]()
            for line in lines:
                con.print(line)
            if not lines:
                con.print(f"[dim]{name}: nothing to do[/]")
        except Exception as e:  # noqa: BLE001 (one failing job must not stop monitoring)
            log.exception("job %s failed", name)
            audit(f"monitor:{name}", "ERROR", "job", name, {"error": str(e)[:300]})
            con.print(f"[red]{name} failed:[/] {e}")
    return run


def main(once: bool = False, skip_test: bool = False) -> None:
    init_db()
    if once:
        for name in JOBS:
            if not (skip_test and name == "scheduled_test"):
                _safe(name)()
        return
    j, now = cfg()["jobs"], datetime.now()
    sched = BlockingScheduler(executors={"default": ThreadPoolExecutor(1)},
                              job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    sched.add_job(_safe("regulation_watch"), "interval", minutes=j["regulation_watch_minutes"], next_run_time=now)
    sched.add_job(_safe("evidence_watch"), "interval", minutes=j["evidence_watch_minutes"], next_run_time=now)
    sched.add_job(_safe("scheduled_test"), CronTrigger.from_crontab(j["scheduled_test_cron"]))
    sched.add_job(_safe("kri_check"), "interval", minutes=j["kri_minutes"], next_run_time=now)
    con.print("[bold]Monitoring started[/] (Ctrl+C to stop)")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        con.print("stopped")


if __name__ == "__main__":
    typer.run(main)