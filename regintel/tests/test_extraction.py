from regintel.agents import extraction as ex
from regintel.ingestion.clause_tree import build_clauses
from regintel.ingestion.parser import Block as B
from regintel.schemas.common import DocType
from regintel.schemas.regulation import DocVersion

REG = [B("CHAPTER VI"), B("38. Periodic updation shall be carried out as under:"),
       B("(a) at least once in every two years for high risk customers;"),
       B("(b) at least once in every eight years for medium risk customers;"),
       B("39. Definitions"), B("In these Directions terms have their usual meaning.")]


def _doc(blocks, dtype):
    clauses, full = build_clauses(blocks, "D", "V1", "v1", dtype)
    return DocVersion(id="V1", doc_id="D", doc_type=dtype, version_label="v1",
                      source_path="x", sha256="x"), clauses, full


def test_candidates_skip_leadin_keep_children():
    _, clauses, _ = _doc(REG, DocType.REGULATION)
    assert [c.clause_ref for c, _ in ex.obligation_candidates(clauses)] == ["38(a)", "38(b)"]


def test_paraphrased_quote_is_rejected():
    ver, clauses, full = _doc(REG, DocType.REGULATION)

    def fake(task, model, system, user):
        assert "shall be carried out as under" in user          # parent context is passed
        d = {"actor": "RE", "modality": "MUST", "topics": ["KYC"], "confidence": 0.9}
        return model(results=[
            {"clause_key": "C1", "obligations": [{**d, "action": "update high-risk KYC",
                                                  "quote": "at least once in every two years for high risk customers"}]},
            {"clause_key": "C2", "obligations": [{**d, "action": "update medium-risk KYC",
                                                  "quote": "medium risk customers must be updated every 8 years"}]}])

    obls, rejected = ex.extract_obligations(ver, clauses, full, "RUN-T", llm=fake)
    assert len(obls) == 1 and obls[0].source.clause_ref == "38(a)"
    assert rejected[0]["reason"] == "SPAN_NOT_FOUND"


def test_control_id_from_tag():
    blocks = [B("6. Risk Categorisation"),
              B("6.3 Periodic KYC updation - Medium risk [KYC-C10]. Periodic updation of KYC shall be "
                "carried out at least once every ten years for Medium risk customers.")]
    ver, clauses, full = _doc(blocks, DocType.POLICY)

    def fake(task, model, system, user):
        return model(results=[{"clause_key": "C1", "controls": [{
            "name": "Re-KYC medium risk", "objective": "o", "description": "d", "control_type": "DETECTIVE",
            "nature": "HYBRID", "parameters": [{"name": "periodicity", "value": "10", "unit": "years"}],
            "quote": "at least once every ten years for Medium risk customers", "confidence": 0.95}]}])

    ctls, rejected = ex.extract_controls(ver, clauses, full, "RUN-T", llm=fake)
    assert [c.id for c in ctls] == ["KYC-C10"] and ctls[0].parameters[0].value == "10" and not rejected