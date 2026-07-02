# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic DocBook 5 + BibTeX extraction. Stdlib xml.etree, no LLM."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from scripts.pipeline.md_extract import slugify

_NS = "{http://docbook.org/ns/docbook}"
_XLINK = "{http://www.w3.org/1999/xlink}href"
_BIB_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,", re.M)
_BIB_TITLE = re.compile(r"title\s*=\s*[{\"]([^}\"]+)[}\"]", re.I)


def _edge(s: str, r: str, o: str, relpath: str, lineno: int = 1) -> dict:
    return {"s": s, "r": r, "o": o, "c": 1.0, "prov": f"{relpath}:{lineno}"}


def _walk(elem: ET.Element, parent_id: str, relpath: str, edges: list[dict]) -> None:
    for child in elem:
        if child.tag == f"{_NS}section":
            title_el = child.find(f"{_NS}title")
            title = (title_el.text or "").strip() if title_el is not None else ""
            node = f"{relpath}#{slugify(title)}" if title else parent_id
            if title:
                edges.append(_edge(parent_id, "contains", node, relpath))
            _walk(child, node, relpath, edges)
        else:
            if child.tag == f"{_NS}citation" and (child.text or "").strip():
                edges.append(_edge(parent_id, "cites", f"cite:{child.text.strip()}", relpath))
            href = child.get(_XLINK)
            if href:
                edges.append(_edge(parent_id, "references", href, relpath))
            _walk(child, parent_id, relpath, edges)


def extract_docbook(text: str, relpath: str) -> list[dict]:
    edges: list[dict] = []
    root = ET.fromstring(text)
    title_el = root.find(f"{_NS}title")
    if title_el is not None and (title_el.text or "").strip():
        edges.append(_edge(relpath, "has-title", title_el.text.strip(), relpath))
    _walk(root, relpath, relpath, edges)
    return edges


def extract_bib(text: str, relpath: str) -> list[dict]:
    edges: list[dict] = []
    lines = text.splitlines()
    entry_line = {}
    for i, line in enumerate(lines, start=1):
        m = _BIB_ENTRY.search(line)
        if m:
            entry_line[m.group(1)] = i
    for key, start in entry_line.items():
        # search entry body (from its @ line to next entry or EOF) for title
        starts = sorted(entry_line.values())
        nxt = min([s for s in starts if s > start], default=len(lines) + 1)
        body = "\n".join(lines[start - 1:nxt - 1])
        tm = _BIB_TITLE.search(body)
        if tm:
            edges.append(_edge(f"cite:{key}", "has-title", tm.group(1).strip(),
                               relpath, start))
    return sorted(edges, key=lambda e: (e["s"], e["r"], e["o"]))
