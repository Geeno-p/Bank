import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from regintel import db
from regintel.graph import flow
from regintel.settings import get_settings


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    db.init_db()
    monkeypatch.setattr(flow, "load_extracted", lambda *a, **k: [])
    yield
    get_settings.cache_clear()


def _stub(monkeypatch, name, out=None):
    monkeypatch.setitem(flow.NODES, name, flow.step(name)(lambda s: dict(out or {})))


def _start(app, event, thread):
    cfg = {"configurable": {"thread_id": thread}}
    app.invoke({"run_id": thread, "event_type": event, "payload": {}, "no_kg": True, "trace": []}, cfg)
    return cfg


def test_new_version_pauses_then_commits(monkeypatch):
    for n in ("ingest", "applicability", "map_new", "critic"):
        _stub(monkeypatch, n)
    _stub(monkeypatch, "change", {"material": 1, "new_obligation_ids": ["O1"]})
    _stub(monkeypatch, "assess", {"review_ids": ["GAP-1"], "impacted_gap_ids": ["GAP-1"]})
    app = flow.build(MemorySaver())
    cfg = _start(app, "NEW_VERSION", "T1")
    assert app.get_state(cfg).next == ("hitl",)                       # paused for human
    out = app.invoke(Command(resume={"action": "approve", "reviewer": "tester"}), cfg)
    assert out["status"] == "COMMITTED"
    assert [t["node"] for t in out["trace"]] == ["router", "ingest", "change", "applicability",
                                                 "map_new", "assess", "critic", "hitl", "commit"]


def test_short_circuits(monkeypatch):
    _stub(monkeypatch, "ingest")
    _stub(monkeypatch, "change", {"material": 0})
    app = flow.build(MemorySaver())
    assert app.get_state(_start(app, "NEW_VERSION", "T2")).values["status"] == "NO_MATERIAL_CHANGE"
    assert app.get_state(_start(app, "WHAT_IF", "T3")).values["status"] == "UNSUPPORTED"