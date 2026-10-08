"""Single entry point for all LLM calls: routing + Instructor validation + SQLite cache + throttle."""
from __future__ import annotations

import hashlib
import json
import time
from functools import lru_cache
from typing import TypeVar

import instructor
from pydantic import BaseModel

from regintel.config import get_model_routing
from regintel.db import get_conn
from regintel.schemas.common import utcnow
from regintel.settings import get_settings

T = TypeVar("T", bound=BaseModel)

OPENAI_COMPATIBLE = {  # provider -> base_url (None = OpenAI default)
    "openai": None,
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "__settings__",
}
DEFAULT_MODE = {"openai": "tools", "groq": "tools", "google": "md_json", "ollama": "json"}
MODES = {"tools": instructor.Mode.TOOLS, "json": instructor.Mode.JSON, "md_json": instructor.Mode.MD_JSON}
_last_call: dict[str, float] = {}


@lru_cache
def _ensure_cache() -> None:
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, task TEXT, "
                     "model TEXT, response TEXT NOT NULL, created_at TEXT NOT NULL)")


def _key_for(provider: str) -> str:
    s = get_settings()
    secret = {"openai": s.openai_api_key, "google": s.google_api_key, "groq": s.groq_api_key,
              "anthropic": s.anthropic_api_key}.get(provider)
    if provider == "ollama":
        return "ollama"
    if not secret:
        raise RuntimeError(f"{provider.upper()}_API_KEY missing in .env")
    return secret.get_secret_value()


@lru_cache
def _client(provider: str, mode: str):
    if provider == "anthropic":
        from anthropic import Anthropic
        return instructor.from_anthropic(Anthropic(api_key=_key_for(provider)))
    from openai import OpenAI
    base = OPENAI_COMPATIBLE[provider]
    if base == "__settings__":
        base = get_settings().ollama_base_url
    return instructor.from_openai(OpenAI(api_key=_key_for(provider), base_url=base), mode=MODES[mode])


def _throttle(provider: str, rpm: int | None) -> None:
    if rpm:
        wait = _last_call.get(provider, 0.0) + 60.0 / rpm - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    _last_call[provider] = time.monotonic()


def _is_rate_limit(msg: str) -> bool:
    m = msg.lower()
    hard_quota = any(x in m for x in ("insufficient_quota", "credit", "billing"))
    return not hard_quota and any(x in m for x in ("429", "rate limit", "resource_exhausted", "too many"))


def call_llm(task: str, response_model: type[T], system: str, user: str,
             use_cache: bool = True, max_retries: int = 2) -> T:
    tier = get_model_routing().for_task(task)
    mode = tier.mode or DEFAULT_MODE.get(tier.provider, "tools")
    schema = json.dumps(response_model.model_json_schema(), sort_keys=True)
    key = hashlib.sha256("\x1f".join([tier.provider, tier.model, schema, system, user]).encode()).hexdigest()
    _ensure_cache()
    if use_cache:
        with get_conn() as conn:
            row = conn.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        if row:
            return response_model.model_validate_json(row["response"])

    for attempt in range(4):
        _throttle(tier.provider, tier.rpm)
        try:
            result = _client(tier.provider, mode).chat.completions.create(
                model=tier.model, response_model=response_model, max_retries=max_retries,
                temperature=tier.temperature, max_tokens=tier.max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            break
        except Exception as e:  # noqa: BLE001
            if attempt < 3 and _is_rate_limit(str(e)):
                time.sleep(15 * (attempt + 1))        # 15s, 30s, 45s back-off
                continue
            raise

    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?, ?)",
                     (key, task, tier.model, result.model_dump_json(), utcnow().isoformat()))
    return result