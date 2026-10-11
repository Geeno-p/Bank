import pytest

from regintel.kg.graph_tools import _coverage_change, text_to_cypher
from regintel.kg.guard import GuardError, validate


@pytest.mark.parametrize("q", [
    "MATCH (n) DETACH DELETE n",
    "CREATE (:X)",
    "MATCH (n) SET n.x = 1 RETURN n",
    "MATCH (n) RETURN n; MATCH (m) DELETE m",
    "CALL apoc.periodic.iterate('a', 'b', {})",
    "LOAD CSV FROM 'file:///x.csv' AS r RETURN r",
    "PROFILE MATCH (n) RETURN n",
    "SHOW USERS",
    "MATCH (n) // harmless\nREMOVE n.x",
])
def test_guard_blocks_writes_and_admin(q):
    with pytest.raises(GuardError):
        validate(q)


def test_guard_allows_reads_and_caps_rows():
    q = validate("MATCH (c:Control) WHERE c.name = 'SET up; DELETE' RETURN c.id, c.created_at")
    assert q.endswith("LIMIT 50")                                  # keywords inside strings are fine
    assert validate("MATCH (c:Control) RETURN c.id LIMIT 5").endswith("LIMIT 5")
    assert validate("CALL db.labels() YIELD label RETURN label")


def test_coverage_change():
    ms = [{"control": "C1", "coverage": "FULL"}, {"control": "C2", "coverage": "PARTIAL"}]
    assert _coverage_change(ms, "C1") == ("FULL", "PARTIAL")
    assert _coverage_change(ms[:1], "C1") == ("FULL", "NONE")


def test_text_to_cypher_self_corrects():
    drafts = iter(["MATCH (n) DETACH DELETE n", "MATCH (c:Control) RETURN c.id AS id"])
    prompts = []

    def fake(task, model, system, user):
        prompts.append(user)
        return model(cypher=next(drafts), explanation="controls")

    execute = lambda c: {"cypher": validate(c), "columns": ["id"], "rows": [{"id": "C1"}], "truncated": False}  # noqa: E731
    res = text_to_cypher("list controls", llm=fake, execute=execute, schema="(test schema)")
    assert res["attempts"] == 2 and res["rows"] == [{"id": "C1"}]
    assert "ERROR" in prompts[1] and "DETACH" in prompts[1]        # the error was fed back to the LLM