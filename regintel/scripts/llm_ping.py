"""Check API key + model routing:  python scripts/llm_ping.py"""
from pydantic import BaseModel

from regintel.config import get_model_routing
from regintel.llm.client import call_llm


class Pong(BaseModel):
    ok: bool
    word: str


for task in ("obligation_extraction", "mapping_judge"):  # small tier, big tier
    tier = get_model_routing().for_task(task)
    r = call_llm(task, Pong, "You are a test endpoint.", "Return ok=true and word='pong'.", use_cache=False)
    print(f"{task:24} -> {tier.provider}/{tier.model}: {r}")