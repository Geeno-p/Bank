import json

from regintel.guards.span_verifier import find_span, make_span
from regintel.kg.repository import props
from regintel.schemas import DocType, Modality, Obligation, Parameter, Provenance, SourceSpan
from regintel.schemas.regulation import Clause

TEXT = ("38. Periodic updation\nPeriodic updation shall be carried out at least once in "
        "every two years for high risk customers.\nThe RE\u2019s Board shall approve the policy.")


def test_line_wrap_case_and_exact_source_text():
    s, e = find_span("periodic updation shall be  carried out\nat least once", TEXT)
    assert TEXT[s:e] == "Periodic updation shall be carried out at least once"


def test_curly_quotes_and_trailing_punctuation():
    s, e = find_span("\"the RE's board shall approve the policy.\"", TEXT)
    assert TEXT[s:e] == "The RE\u2019s Board shall approve the policy"


def test_rejects_paraphrase_short_and_out_of_bounds():
    assert find_span("updation must happen every 2 years", TEXT) is None   # paraphrase
    assert find_span("shall", TEXT) is None                                # too short
    assert find_span("Board shall approve the policy", TEXT, 0, 60) is None


def test_make_span_inside_clause():
    clause = Clause(id="CL-1", doc_id="RBI-KYC-MD", version_id="V1", doc_type=DocType.REGULATION,
                    clause_ref="38", text=TEXT[:130], char_start=0, char_end=130, content_hash="x")
    span = make_span("carried out at least once in every two years", clause, TEXT, "v1")
    assert span and span.clause_ref == "38" and TEXT[span.char_start:span.char_end] == span.quote


def test_props_flattens_nested_for_neo4j():
    o = Obligation(id="OBL-1", regulation_id="R", clause_id="CL-1", actor="RE", modality=Modality.MUST,
                   action="update KYC", topics=["KYC"], confidence=0.9,
                   parameters=[Parameter(name="frequency", value="2", unit="years")],
                   source=SourceSpan(doc_id="R", version="v1", clause_ref="38(a)", quote="at least once"),
                   provenance=Provenance(created_by="agent:test"))
    p = props(o)
    assert json.loads(p["source"])["clause_ref"] == "38(a)"
    assert isinstance(p["parameters"], str) and p["topics"] == ["KYC"]