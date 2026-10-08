from regintel.agents.change import diff_clauses
from regintel.ingestion.clause_tree import build_clauses
from regintel.ingestion.parser import Block as B
from regintel.schemas.common import DocType

BASE = ["CHAPTER IV Risk", "12. Periodic updation of KYC shall be carried out as under:",
        "(a) at least once in every two years for high risk customers;",
        "(b) at least once in every eight years for medium risk customers;",
        "17. The Reserve Bank may issue clarifications on these Directions from time to time.",
        "18. Every RE shall maintain a register of legacy accounts opened before 2005."]
AMENDED = [BASE[0], BASE[1], "(a) at least once in every year for high risk customers;", BASE[3],
           "17. The Reserve Bank may, from time to time, issue clarifications on these Directions.",
           "19. Every RE shall appoint a KYC nodal officer for each region."]


def _cl(lines, label):
    return build_clauses([B(x) for x in lines], "RBI-KYC-MD", f"V-{label}", label, DocType.REGULATION)[0]


def test_diff_classifies_and_calls_llm_once():
    old, new = _cl(BASE, "v1"), _cl(AMENDED, "v2")
    calls = []

    def fake(task, model, system, user):
        calls.append(user)
        return model(change_type="TIGHTENED", summary="High-risk re-KYC now yearly", confidence=0.9,
                     changed_parameters=[{"name": "periodicity", "old": "two years", "new": "one year"}])

    changes, unchanged = diff_clauses(old, new, "RBI-KYC-MD", llm=fake)
    refs = {c.id: c.clause_ref for c in old + new}
    got = {refs[c.new_clause_id or c.old_clause_id]: c.change_type.value for c in changes}
    assert got == {"12(a)": "TIGHTENED", "17": "EDITORIAL", "18": "DELETED", "19": "NEW"}
    assert len(calls) == 1 and "every year" in calls[0]          # only the real change hits the LLM
    assert unchanged == 3                                         # chapter, 12, 12(b)
    tight = next(c for c in changes if c.change_type.value == "TIGHTENED")
    assert tight.is_material and tight.changed_parameters[0].new == "one year"