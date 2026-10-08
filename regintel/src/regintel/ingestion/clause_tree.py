"""Ordered text blocks -> hierarchical Clause list with exact char offsets into text.txt."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from collections import Counter
from regintel.ingestion.parser import Block
from regintel.schemas.common import DocType, content_hash, stable_id
from regintel.schemas.regulation import Clause

ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"}
END = r"(?:\s|$)"
PATTERNS = [  # (kind, regex): order matters
    ("chapter", re.compile(r"^(CHAPTER|Chapter|PART|Part|ANNEXURE|Annexure|Annex|SCHEDULE|Schedule)\s+([A-Z0-9]+)\b")),
    ("annex_item", re.compile(rf"^([A-Z])\.(\d+){END}")),
    ("dotted", re.compile(rf"^(\d{{1,3}}(?:\.\d{{1,3}})+)\.?{END}")),   # 6.2  (policies)
    ("section", re.compile(rf"^(\d{{1,3}}[A-Z]?)\.{END}")),             # 38.  (regulations)
    ("paren_num", re.compile(rf"^\((\d{{1,3}}[A-Z]?)\){END}")),          # (1)
    ("paren_alpha", re.compile(rf"^\(([a-z]{{1,4}})\){END}")),           # (a) or (iv)
]
SEC_PLUS_SUB = re.compile(r"^(\d{1,3}[A-Z]?\.)\s+(\(\d{1,3}\)\s.*)$")  # "8. (1) A Data Fiduciary..."
MARKER = re.compile(r"^\S+\s+")


@dataclass
class _Node:
    ref: str
    level: int
    parent: int | None
    blocks: list[int] = field(default_factory=list)


def _classify(text: str, last_alpha: str | None) -> tuple[int, str] | None:
    for kind, rx in PATTERNS:
        if not (m := rx.match(text)):
            continue
        g = m.groups()
        if kind == "chapter":
            return 0, f"{g[0].title()} {g[1]}"
        if kind == "annex_item":
            return 1, f"{g[0]}.{g[1]}"
        if kind == "dotted":
            return 1 + g[0].count("."), g[0]
        if kind == "section":
            return 1, g[0]
        if kind == "paren_num":
            return 2, f"({g[0]})"
        tok = g[0]  # "(i)" right after "(h)" is a letter, otherwise a roman numeral
        follows_letter = last_alpha is not None and len(tok) == 1 and ord(tok) == ord(last_alpha) + 1
        return (4 if tok in ROMAN and not follows_letter else 3), f"({tok})"
    return None


def _ref(token: str, parent: _Node | None) -> str:
    if token.startswith("(") and parent is not None and parent.level >= 1:
        return parent.ref + token          # 38 + (a) + (i) -> 38(a)(i)
    return token


def _presplit(blocks: list[Block]) -> list[Block]:
    out = []
    for b in blocks:
        if m := SEC_PLUS_SUB.match(b.text):
            out += [Block(m.group(1), b.page, b.is_heading), Block(m.group(2), b.page)]
        else:
            out.append(b)
    return out


def build_clauses(blocks: list[Block], doc_id: str, version_id: str, version_label: str,
                  doc_type: DocType) -> tuple[list[Clause], str]:
    blocks = _presplit(blocks)
    full = "\n".join(b.text for b in blocks)
    starts, pos = [], 0
    for b in blocks:
        starts.append(pos)
        pos += len(b.text) + 1

    # 1) build the tree: numbered blocks open nodes; other blocks continue the current node
    nodes, stack, last_alpha = [_Node("Preamble", 0, None)], [], None
    for i, b in enumerate(blocks):
        cls = _classify(b.text, last_alpha)
        if cls is None and b.is_heading and not stack:
            cls = (0, b.text[:60])                        # document title becomes the root
        if cls is None:
            nodes[stack[-1] if stack else 0].blocks.append(i)
            continue
        level, token = cls
        if level == 3 and len(token) == 3:
            last_alpha = token[1]
        elif level <= 2:
            last_alpha = None
        while stack and nodes[stack[-1]].level >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        nodes.append(_Node(_ref(token, nodes[parent] if parent is not None else None), level, parent, [i]))
        stack.append(len(nodes) - 1)

    # 2) stable, unique IDs
    # 2) stable, unique IDs; duplicate refs get chapter-qualified ("Annex I/1")
    def _chapter(idx: int) -> str | None:
        p = nodes[idx].parent
        while p is not None and nodes[p].level != 0:
            p = nodes[p].parent
        return nodes[p].ref if p is not None else None

    counts = Counter(n.ref for n in nodes if n.blocks)
    ids, refs, seen = {}, {}, {}
    for idx, n in enumerate(nodes):
        if not n.blocks:
            continue
        ref = n.ref
        if counts[ref] > 1 and n.level > 0 and (ch := _chapter(idx)):
            ref = f"{ch}/{ref}"
        k = seen[ref] = seen.get(ref, 0) + 1
        refs[idx] = ref if k == 1 else f"{ref}#{k}"
        ids[idx] = stable_id("CL", doc_id, version_label, refs[idx])

    # 3) emit clauses (each node's blocks are contiguous -> exact offsets)
    clauses = []
    for idx, n in enumerate(nodes):
        if idx not in ids:
            continue
        cs = starts[n.blocks[0]]
        ce = starts[n.blocks[-1]] + len(blocks[n.blocks[-1]].text)
        text = full[cs:ce]
        path, p = [], n.parent
        while p is not None:
            path.insert(0, blocks[nodes[p].blocks[0]].text[:80])
            p = nodes[p].parent
        pages = [blocks[j].page for j in n.blocks if blocks[j].page is not None]
        clauses.append(Clause(
            id=ids[idx], doc_id=doc_id, version_id=version_id, doc_type=doc_type,
            clause_ref=refs[idx], heading_path=path, level=n.level, parent_id=ids.get(n.parent),
            text=text, page_start=min(pages, default=None), page_end=max(pages, default=None),
            char_start=cs, char_end=ce,
            content_hash=content_hash(MARKER.sub("", text, count=1)),  # renumbering doesn't change the hash
        ))
    return clauses, full

def with_ancestor_context(clauses: list[Clause]) -> dict[str, str]:
    """clause_id -> ancestors' full text + own text. Gives '(a) ...' its 'shall ... as under:' lead-in."""
    by_id = {c.id: c for c in clauses}
    out = {}
    for c in clauses:
        chain, p = [], c.parent_id
        while p:
            chain.insert(0, by_id[p].text)
            p = by_id[p].parent_id
        out[c.id] = "\n".join([*chain, c.text])
    return out