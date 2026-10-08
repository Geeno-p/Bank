from datetime import date
from typing import Literal

from regintel.schemas.common import Base


class RegVersionSource(Base):
    label: str
    path: str
    effective_date: date | None = None


class RegulationSource(Base):
    id: str
    title: str
    issuer: str
    doc_type: str
    jurisdiction: str = "IN"
    topics: list[str] = []
    versions: list[RegVersionSource]


class SourcesConfig(Base):
    regulations: list[RegulationSource]


class Topic(Base):
    id: str
    label: str
    keywords: list[str] = []


class Taxonomy(Base):
    topics: list[Topic]

    def ids(self) -> set[str]:
        return {t.id for t in self.topics}


class ModelTier(Base):
    provider: Literal["openai", "anthropic", "google", "groq", "ollama"]
    model: str
    temperature: float = 0.0
    max_tokens: int = 2000
    mode: Literal["tools", "json", "md_json"] | None = None   # how Instructor gets structured output
    rpm: int | None = None # client-side throttle (free tiers)

class ModelRouting(Base):
    tiers: dict[str, ModelTier]
    routing: dict[str, str]

    def for_task(self, task: str) -> ModelTier:
        tier = self.routing.get(task, "small")
        return self.tiers[tier]