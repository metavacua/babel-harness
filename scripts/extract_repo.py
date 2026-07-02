#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extract a git repository into a .larql.json graph + coverage + provenance.

Usage: python3 scripts/extract_repo.py <repo_root> --out <dir>
Exit codes: 0 ok; 2 = DAG well-formedness violations or empty corpus (details on stderr).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline.corpus import build_corpus          # noqa: E402
from scripts.pipeline.dag import check, depths            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_root", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    res = build_corpus(args.repo_root)
    if not res["edges"]:
        print("error: empty corpus (no extractable files)", file=sys.stderr)
        return 2
    violations = check(res["edges"])
    cycles = [v for v in violations if v.startswith("cycle")]
    if cycles:
        # cycles break depth computation and λ — fatal (design §5)
        for v in cycles:
            print(f"dag-violation: {v}", file=sys.stderr)
        return 2
    # reachability violations are QUARANTINED, never fatal and never dropped (design §4/§5):
    # real corpora legitimately contain uncited bib entries, placeholder dirs, external URLs
    for v in violations:
        print(f"dag-quarantine: {v}", file=sys.stderr)

    args.out.mkdir(parents=True, exist_ok=True)
    graph = {"larql_version": "1.0",
             "metadata": {"source_repo": str(args.repo_root),
                          "extractor": "babel-harness scripts/extract_repo.py",
                          "max_depth": max(depths(res["edges"]).values())},
             "schema": {},
             "edges": [{"s": e["s"], "r": e["r"], "o": e["o"], "c": e["c"]}
                        for e in res["edges"]]}
    (args.out / "graph.larql.json").write_text(json.dumps(graph, indent=1))
    (args.out / "coverage.json").write_text(json.dumps(
        {"coverage": res["coverage"], "quarantine": res["quarantine"],
         "dag_violations": violations}, indent=1))
    with (args.out / "provenance.jsonl").open("w") as fh:
        for e in res["edges"]:
            fh.write(json.dumps({"s": e["s"], "r": e["r"], "o": e["o"],
                                 "prov": e["prov"]}) + "\n")
    print(f"edges={len(res['edges'])} files={len(res['coverage'])} "
          f"quarantined={len(res['quarantine'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
