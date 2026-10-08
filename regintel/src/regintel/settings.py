from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    embedding_backend: str = "auto"      # auto | local | none
    kg_write_default: bool = True
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"
    sqlite_path: str = "data/regintel.db"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("regintel_pass")
    postgres_dsn: str = "postgresql://regintel:regintel_pass@localhost:5432/regintel"
    
    google_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434/v1"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    e2b_api_key: SecretStr | None = None

    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    @property
    def config_dir(self) -> Path:
        return PROJECT_ROOT / "config"

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def prompts_dir(self) -> Path:
        return PROJECT_ROOT / "prompts"
    
    @property
    def sqlite_file(self) -> Path:
        p = Path(self.sqlite_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

@lru_cache
def get_settings() -> Settings:
    return Settings()