# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic markdown -> edge extraction. Stdlib only, no LLM.

Node IDs: file = repo-relative path; section = "path#slug".
Relations: contains, references, has-title.
Every edge carries provenance "relpath:lineno".
"""
from __future__ import annotations

import posixpath
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^(```|~~~)")


def slugify(text: str) -> str:
    """GitHub-style anchor slug: lowercase, drop non-word (keep hyphens), spaces->hyphens."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _edge(s: str, r: str, o: str, relpath: str, lineno: int) -> dict:
    return {"s": s, "r": r, "o": o, "c": 1.0, "prov": f"{relpath}:{lineno}"}


def _resolve(target: str, relpath: str) -> str | None:
    """Resolve a relative link target to a repo-relative path (+ optional #anchor)."""
    if target.startswith(("http://", "https://", "mailto:")):
        return None
    path_part, _, anchor = target.partition("#")
    if not path_part:  # same-file anchor
        resolved = relpath
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relpath), path_part))
        if resolved.startswith(".."):
            return None
    return f"{resolved}#{anchor}" if anchor else resolved


def extract_markdown(text: str, relpath: str) -> list[dict]:
    edges: list[dict] = []
    # stack of (level, node_id); level 0 = the file itself
    stack: list[tuple[int, str]] = [(0, relpath)]
    in_fence = False
    saw_title = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            if level == 1 and not saw_title:
                edges.append(_edge(relpath, "has-title", title, relpath, lineno))
                saw_title = True
                continue
            node = f"{relpath}#{slugify(title)}"
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else relpath
            edges.append(_edge(parent, "contains", node, relpath, lineno))
            stack.append((level, node))
            continue
        for lm in _LINK.finditer(line):
            resolved = _resolve(lm.group(1), relpath)
            if resolved:
                src = stack[-1][1] if stack else relpath
                edges.append(_edge(src, "references", resolved, relpath, lineno))
    return edges
