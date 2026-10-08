from datetime import date

from pydantic import Field, computed_field

from regintel.schemas.common import Base, DocType, Temporal


class Regulation(Base):
    id: str
    title: str
    issuer: str
    doc_type: str
    jurisdiction: str = "IN"
    topics: list[str] = []


class DocVersion(Temporal):
    """A specific version of a regulation OR a policy document."""
    id: str
    doc_id: str
    doc_type: DocType
    version_label: str
    effective_date: date | None = None
    source_path: str
    sha256: str


class Clause(Base):
    """Smallest addressable unit. One clause = one chunk."""
    id: str
    doc_id: str
    version_id: str
    doc_type: DocType
    clause_ref: str                         # "Sec 38(a)" / "KYC-POL 5.3"
    heading_path: list[str] = Field(default_factory=list)
    level: int = 0
    parent_id: str | None = None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    content_hash: str

    @computed_field
    @property
    def context_text(self) -> str:
        """Text used for embedding: heading breadcrumb + body."""
        crumb = " > ".join(self.heading_path)
        return f"{crumb}\n{self.text}" if crumb else self.text