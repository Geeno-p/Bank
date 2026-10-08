import pytest

from regintel import db, deploy
from regintel.settings import get_settings
from test_phase3a import ctl, obl          # reuse helpers (same tests folder)


@pytest.fixture(autouse=True)
def clean():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_bm25_fallback_without_embeddings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "none")
    from regintel.retrieval.hybrid import ControlIndex, resolve_embed
    assert resolve_embed() is None
    idx = ControlIndex([ctl("C1", "Periodic KYC updation of customers", ["KYC"]),
                        ctl("C2", "Sanctions screening before account activation", ["AML_CFT"])])
    assert idx.search(obl("screen customer against sanctions lists", topics=["AML_CFT"]))[0].control.id == "C2"


def test_llm_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "a.db"))
    from regintel.settings import Settings
    monkeypatch.setattr(Settings, "data_dir", property(lambda s: tmp_path))
    db.init_db()
    with db.get_conn() as c:
        c.execute("INSERT INTO llm_cache VALUES ('k', 't', 'm', '{}', 'now')")
    assert deploy.export_cache() == 1
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "b.db"))
    get_settings.cache_clear()
    assert deploy.bootstrap()["llm_cache_seeded"] == 1