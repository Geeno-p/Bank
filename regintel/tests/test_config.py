from regintel.config import get_bank_profile, get_model_routing, get_sources, get_taxonomy


def test_bank_profile_loads():
    bp = get_bank_profile()
    assert bp.id == "BUB"
    assert bp.has("processes_personal_data")
    assert bp.customer_base.risk_distribution["HIGH"] == 1840


def test_sources_topics_exist_in_taxonomy():
    topic_ids = get_taxonomy().ids()
    for reg in get_sources().regulations:
        assert set(reg.topics) <= topic_ids, f"{reg.id} has unknown topics"


def test_model_routing():
    r = get_model_routing()
    assert r.for_task("mapping_judge").model == r.tiers["big"].model
    assert r.for_task("unknown_task").model == r.tiers["small"].model