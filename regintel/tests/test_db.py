import sqlite3

import pytest

from regintel import db
from regintel.settings import get_settings


@pytest.fixture(autouse=True)
def tmp_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    db.init_db()
    yield
    get_settings.cache_clear()


def test_audit_write_and_read():
    db.audit("agent:test", "CREATE", "obligation", "OBL-1", {"k": "v"}, run_id="R1")
    trail = db.audit_trail("obligation", "OBL-1")
    assert len(trail) == 1 and trail[0]["actor"] == "agent:test"


def test_audit_log_is_append_only():
    db.audit("agent:test", "CREATE", "obligation", "OBL-2")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM audit_log")