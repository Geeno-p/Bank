from functools import lru_cache
from typing import Any

import yaml

from regintel.schemas.bank import BankProfile
from regintel.schemas.config_models import ModelRouting, SourcesConfig, Taxonomy
from regintel.settings import get_settings


@lru_cache
def load_yaml(name: str) -> dict[str, Any]:
    path = get_settings().config_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Config file missing: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def get_bank_profile() -> BankProfile:
    return BankProfile.model_validate(load_yaml("bank_profile.yaml"))


@lru_cache
def get_sources() -> SourcesConfig:
    return SourcesConfig.model_validate(load_yaml("sources.yaml"))


@lru_cache
def get_taxonomy() -> Taxonomy:
    return Taxonomy.model_validate(load_yaml("taxonomy.yaml"))


@lru_cache
def get_model_routing() -> ModelRouting:
    return ModelRouting.model_validate(load_yaml("models.yaml"))


@lru_cache
def get_risk_weights() -> dict[str, Any]:
    return load_yaml("risk_weights.yaml")