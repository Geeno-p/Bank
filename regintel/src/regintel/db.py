"""SQLite operational store: runs, audit log (append-only), review queue, LLM cache."""
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from regintel.settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'RUNNING',
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    data        TEXT,
    run_id      TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_run ON audit_log(run_id);

CREATE TRIGGER IF NOT EXISTS prevent_audit_log_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'append-only: deletion from audit_log is not allowed');
END;

CREATE TRIGGER IF NOT EXISTS prevent_audit_log_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'append-only: updates to audit_log are not allowed');
END;

CREATE TABLE IF NOT EXISTS review_queue (
    id             TEXT PRIMARY KEY,
    item_type      TEXT NOT NULL,
    item_id        TEXT NOT NULL,
    reason         TEXT NOT NULL,
    confidence     REAL,
    status         TEXT NOT NULL DEFAULT 'PENDING',
    reviewer       TEXT,
    decision_notes TEXT,
    run_id         TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_status ON review_queue(status);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    task       TEXT,
    model      TEXT,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Commits on success, rolls back on error, always closes."""
    path = get_settings().sqlite_file
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)          # wait instead of failing if briefly locked
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")      # readers don't block the writer
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def audit(actor: str, action: str, entity_type: str, entity_id: str,
          data: dict[str, Any] | None = None, run_id: str | None = None) -> None:
    data_str = json.dumps(data, default=str) if data is not None else None   # dates/enums safe
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor, action, entity_type, entity_id, data, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (actor, action, entity_type, entity_id, data_str, run_id),
        )


def audit_trail(entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = ? AND entity_id = ? "
            "ORDER BY timestamp ASC, id ASC",
            (entity_type, entity_id),
        ).fetchall()
    return [dict(r) for r in rows]