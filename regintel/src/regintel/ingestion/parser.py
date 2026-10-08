"""Document -> ordered text blocks. Docling first; python-docx / PyMuPDF as fast fallback."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)
SKIP_LABELS = {"page_header", "page_footer", "picture", "caption"}
NOISE = re.compile(r"^(page\s*\d+(\s*of\s*\d+)?|\d{1,4})$", re.I)  # page numbers


@dataclass
class Block:
    text: str
    page: int | None = None
    is_heading: bool = False


@lru_cache(maxsize=1)
def _converter():
    from docling.document_converter import DocumentConverter
    return DocumentConverter()


def _docling(path: Path) -> list[Block]:
    doc = _converter().convert(str(path)).document
    out = []
    for item, _ in doc.iterate_items():
        text = getattr(item, "text", None)
        if not text:
            continue
        label = getattr(item.label, "value", str(item.label))
        if label in SKIP_LABELS:
            continue
        marker = getattr(item, "marker", "") or ""          # Docling may strip "(a)" from list items
        if label == "list_item" and marker and not text.startswith(marker):
            text = f"{marker} {text}"
        prov = getattr(item, "prov", None)
        out.append(Block(text, prov[0].page_no if prov else None, label in {"section_header", "title"}))
    return out


def _docx(path: Path) -> list[Block]:
    from docx import Document
    return [Block(p.text, None, p.style.name.lower().startswith(("heading", "title")))
            for p in Document(str(path)).paragraphs if p.text.strip()]


def _pdf(path: Path) -> list[Block]:
    import fitz  # PyMuPDF
    out = []
    with fitz.open(str(path)) as doc:
        for pno, page in enumerate(doc, 1):
            out += [Block(line, pno) for line in page.get_text("text").splitlines()]
    return out


def parse_document(path: Path, engine: str = "docling") -> list[Block]:
    blocks = None
    if engine == "docling":
        try:
            blocks = _docling(path)
        except Exception as e:  # noqa: BLE001
            log.warning("Docling failed on %s (%s); using fast parser", path.name, e)
    if blocks is None:
        blocks = _docx(path) if path.suffix.lower() == ".docx" else _pdf(path)
    cleaned = []
    for b in blocks:
        t = " ".join(b.text.split())
        if t and not NOISE.match(t):
            cleaned.append(Block(t, b.page, b.is_heading))
    return cleaned