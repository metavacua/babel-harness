# SPDX-License-Identifier: AGPL-3.0-or-later
"""Corpus assembly: git-tracked files -> extracted edges + START/END + coverage."""
from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.pipeline.docbook_extract import extract_bib, extract_docbook
from scripts.pipeline.md_extract import extract_markdown
from scripts.pipeline.path_edges import path_derived_edges

_EXTRACTORS = {".md": extract_markdown, ".xml": extract_docbook, ".bib": extract_bib}


def _git_files(repo_root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo_root), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line and not line.endswith(".gitkeep")]


def build_corpus(repo_root: Path) -> dict:
    edges: list[dict] = []
    coverage: list[dict] = []
    quarantine: list[dict] = []
    files = _git_files(repo_root)

    for rel in sorted(files):
        ext = Path(rel).suffix
        fn = _EXTRACTORS.get(ext)
        if fn is None:
            coverage.append({"path": rel, "status": "skipped",
                             "reason": "unsupported-extension"})
            continue
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
            file_edges = fn(text, rel)
            edges.extend(file_edges)
            coverage.append({"path": rel, "status": "extracted",
                             "edge_count": len(file_edges)})
        except Exception as exc:  # parse failures are quarantined, never dropped silently
            quarantine.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})
            coverage.append({"path": rel, "status": "quarantined",
                             "reason": type(exc).__name__})

    edges.extend(path_derived_edges(sorted(files)))

    # START edges: every top-level path component
    tops = sorted({p.split("/")[0] for p in files})
    for t in tops:
        edges.append({"s": "START", "r": "contains", "o": t, "c": 1.0, "prov": "corpus:0"})
    # END edges: every node with no outgoing `contains`
    has_children = {e["s"] for e in edges if e["r"] == "contains"}
    all_contained = {e["o"] for e in edges if e["r"] == "contains"}
    for leaf in sorted(all_contained - has_children):
        if leaf.startswith(("cite:", "category:", "matter:")):
            continue
        edges.append({"s": leaf, "r": "terminal", "o": "END", "c": 1.0, "prov": "corpus:0"})

    # dedupe + canonical order
    seen: set[tuple] = set()
    unique: list[dict] = []
    for e in sorted(edges, key=lambda e: (e["s"], e["r"], e["o"])):
        key = (e["s"], e["r"], e["o"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return {"edges": unique, "coverage": coverage, "quarantine": quarantine}
