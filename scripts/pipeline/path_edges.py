# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edges derivable from repository paths alone. Deterministic."""
from __future__ import annotations

import posixpath
import re

_MATTER = re.compile(r"^docs/court-record/matters/([^/]+)/")
_CATEGORY = {
    "docs/court-record": "category:court-record",
    "docs/proposals": "category:proposals",
    "docs/cross-cutting": "category:cross-cutting",
    "docs/wip": "category:wip",
    "papers": "category:papers",
}


def path_derived_edges(relpaths: list[str]) -> list[dict]:
    edges: set[tuple] = set()
    for p in sorted(relpaths):
        # directory containment chain
        parts = p.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            child = "/".join(parts[:i + 1])
            edges.add((parent, "contains", child, p))
        m = _MATTER.match(p)
        if m:
            edges.add((p, "part-of-matter", f"matter:{m.group(1)}", p))
        for prefix, cat in _CATEGORY.items():
            if p.startswith(prefix + "/") or posixpath.dirname(p) == prefix:
                edges.add((p, "in-category", cat, p))
                break
    return [{"s": s, "r": r, "o": o, "c": 1.0, "prov": f"{prov}:0"}
            for s, r, o, prov in sorted(edges)]
