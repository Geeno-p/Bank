from regintel.ingestion.parser import Block as B
from regintel.ingestion.parser import parse_document
from regintel.schemas.common import DocType
from regintel.synthetic.policy_writer import write_docx
from regintel.synthetic.spec import KYC_POL
from regintel.ingestion.clause_tree import build_clauses, with_ancestor_context

def test_regulation_hierarchy_and_offsets():
    blocks = [B("CHAPTER VI", is_heading=True), B("38. Periodic Updation"),
              B("Periodic updation shall be carried out as under:"),
              B("(a) high risk customers at least once every two years;"), B("(i) sub-item one"),
              B("(b) medium risk customers;"), B("(h) item h"), B("(i) item after h"),
              B("8. (1) A Data Fiduciary shall process lawfully.")]
    clauses, full = build_clauses(blocks, "REG", "VER-1", "v1", DocType.REGULATION)
    by = {c.clause_ref: c for c in clauses}
    assert list(by) == ["Chapter VI", "38", "38(a)", "38(a)(i)", "38(b)", "38(h)", "38(i)", "8", "8(1)"]
    assert all(full[c.char_start:c.char_end] == c.text for c in clauses)
    assert "Periodic updation shall" in by["38"].text                 # continuation kept
    assert by["38(a)(i)"].parent_id == by["38(a)"].id
    assert by["38(a)(i)"].heading_path[1] == "38. Periodic Updation"


def test_policy_docx_fast(tmp_path):
    path = tmp_path / "KYC-POL.docx"
    write_docx(KYC_POL, path)
    clauses, _ = build_clauses(parse_document(path, "fast"), "KYC-POL", "VER-1", "v4.2", DocType.POLICY)
    c = next(c for c in clauses if c.clause_ref == "6.3")
    assert "ten years" in c.text and "[KYC-C10]" in c.text and c.level == 2
    assert any("Risk Categorisation" in h for h in c.heading_path)
    assert sum("[KYC-C" in c.text for c in clauses) == 14   
    
def test_duplicate_refs_and_ancestor_context():
    blocks = [B("CHAPTER I"), B("1. Short title"), B("3. Records"),
              B("The RE shall preserve the following:"), B("(a) KYC records;"),
              B("Annex I"), B("1. Annex item")]
    clauses, _ = build_clauses(blocks, "REG", "VER-1", "v1", DocType.REGULATION)
    refs = [c.clause_ref for c in clauses]
    assert "Chapter I/1" in refs and "Annex I/1" in refs
    ctx = with_ancestor_context(clauses)[next(c.id for c in clauses if c.clause_ref == "3(a)")]
    assert "shall preserve" in ctx and ctx.endswith("(a) KYC records;")          # all 14 controls found

def test_synthetic_regulation_tree(tmp_path, monkeypatch):
    from regintel.settings import get_settings
    from regintel.synthetic.regulations import write_all
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    monkeypatch.setattr(type(get_settings()), "data_dir", property(lambda s: tmp_path))
    path = write_all()[0]                                   # RBI-KYC-MD
    clauses, _ = build_clauses(parse_document(path, "fast"), "RBI-KYC-MD", "V", "v-demo", DocType.REGULATION)
    by = {c.clause_ref: c for c in clauses}
    assert "eight years" in by["12(b)"].text and by["12(b)"].parent_id == by["12"].id
    get_settings.cache_clear()