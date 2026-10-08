import pytest

from regintel import db
from regintel.monitoring import scheduler as sch
from regintel.schemas.evidence import ControlTestResult
from regintel.settings import get_settings


@pytest.fixture(autouse=True)
def tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    db.init_db()
    yield
    get_settings.cache_clear()


def _tr(exc: int) -> ControlTestResult:
    return ControlTestResult(id="T", control_id="ITO-C06", evidence_id="E", test_type="OPERATING",
                             procedure="p", population=100, exceptions=exc, tolerance=0.05)


def test_kri_breach_then_recover(monkeypatch):
    monkeypatch.setattr(sch, "cfg", lambda: {"kris": [{"id": "K1", "name": "n", "metric": "exception_rate",
                                                       "control_id": "ITO-C06", "threshold": 0.0}]})
    seq = iter([[_tr(14)], [_tr(14)], [_tr(0)]])
    monkeypatch.setattr(sch, "load_extracted", lambda name, model: next(seq) if name == "test_results" else [])
    for _ in range(3):
        sch.kri_check()
    with db.get_conn() as c:
        acts = [r["action"] for r in c.execute("SELECT action FROM audit_log WHERE entity_type='kri' ORDER BY id")]
    assert acts == ["BREACH", "RECOVERED"]                      # audit on change of state only


def test_evidence_watch_baseline_then_trigger(monkeypatch, tmp_path):
    monkeypatch.setattr(type(get_settings()), "data_dir", property(lambda s: tmp_path))
    (tmp_path / "evidence").mkdir()
    f = tmp_path / "evidence" / "a.csv"
    f.write_text("x\n1\n")
    calls = []
    monkeypatch.setattr(sch.service, "start", lambda *a, **k: calls.append(a) or {"run_id": "R", "status": "COMMITTED"})
    assert sch.evidence_watch() == ["evidence_watch: baseline recorded"] and not calls
    f.write_text("x\n2\n")
    sch.evidence_watch()
    assert len(calls) == 1 and calls[0][0] == "EVIDENCE_UPLOADED"
    assert sch.evidence_watch() == [] and len(calls) == 1      # unchanged -> no trigger