# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic DocBook 5 + BibTeX extraction. Stdlib xml.etree, no LLM."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from scripts.pipeline.md_extract import slugify

_NS = "{http://docbook.org/ns/docbook}"
_XLINK = "{http://www.w3.org/1999/xlink}href"
_BIB_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,", re.M)
# Matches the *field name* only; the value is extracted by a balanced-brace /
# quote scanner (_bib_field_value) since BibTeX titles routinely contain
# nested `{...}` groups (e.g. `{{LARQL} --- {Lazarus Query Language}: ...}`)
# that a non-recursive regex cannot capture correctly.
_BIB_FIELD = re.compile(r"(?<![\w-])title\s*=\s*", re.I)


def _bib_field_value(body: str, value_start: int) -> str | None:
    """Extract a BibTeX field value starting at ``value_start`` in ``body``.

    Handles brace-delimited values with arbitrarily nested `{...}` groups and
    quote-delimited `"..."` values. Returns the value with only the
    outermost delimiters stripped, or None if the value is malformed
    (unterminated).
    """
    i = value_start
    n = len(body)
    while i < n and body[i].isspace():
        i += 1
    if i >= n:
        return None
    delim = body[i]
    if delim == "{":
        depth = 0
        start = i + 1
        j = i
        while j < n:
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
                if depth == 0:
                    return body[start:j]
            j += 1
        return None  # unterminated
    if delim == '"':
        start = i + 1
        j = start
        while j < n:
            if body[j] == "\\":
                j += 2
                continue
            if body[j] == '"':
                return body[start:j]
            j += 1
        return None  # unterminated
    return None


def _edge(s: str, r: str, o: str, relpath: str, lineno: int = 1) -> dict:
    return {"s": s, "r": r, "o": o, "c": 1.0, "prov": f"{relpath}:{lineno}"}


def _title_text(title_el: ET.Element) -> str:
    """Full text of a title element, including inline markup (e.g. <emphasis>)."""
    return "".join(title_el.itertext()).strip()


def _walk(elem: ET.Element, parent_id: str, relpath: str, edges: list[dict]) -> None:
    for child in elem:
        if child.tag == f"{_NS}section":
            title_el = child.find(f"{_NS}title")
            title = _title_text(title_el) if title_el is not None else ""
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
    if title_el is not None:
        title = _title_text(title_el)
        if title:
            edges.append(_edge(relpath, "has-title", title, relpath))
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
        fm = _BIB_FIELD.search(body)
        if fm:
            value = _bib_field_value(body, fm.end())
            if value is not None:
                edges.append(_edge(f"cite:{key}", "has-title", value.strip(),
                                   relpath, start))
    return sorted(edges, key=lambda e: (e["s"], e["r"], e["o"]))
