#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Falsifiable claim check for Task 1 of the docs-architecture refactor
(2026-07-05-docs-architecture-refactor-plan.md, Task 1).

Verifies the durable-documentation directory skeleton exists:
  - docs/adr/        (numbered ADRs, extended-Nygard format)
  - docs/specs/       (component contracts/formats)
  - docs/audits/      (dated codebase-health assessments)
  - docs/specs.md     (index table with a header row)
  - docs/adr/0000-template.md (the ADR template, extended-Nygard shape)

Standalone rather than an extension of scripts/spec_proof.py: that script's
CLAIMS list is scoped to one specific spec document
(docs/superpowers/specs/2026-06-27-larql-ddt-framework-design.md) and its own
DDT-framework theorem — bolting unrelated docs-skeleton claims onto it would
misrepresent what that script proves. This is a small standalone check in
the same falsifiable-claim spirit instead.

Usage:
  python3 scripts/docs_skeleton_proof.py          # human-readable
  python3 scripts/docs_skeleton_proof.py --json    # JSON certificate
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent


def _check_dir(rel: str) -> tuple[bool, str]:
    p = REPO / rel
    ok = p.is_dir()
    return ok, f"is_dir({rel}) = {ok}"


def _check_specs_md_header() -> tuple[bool, str]:
    p = REPO / "docs/specs.md"
    if not p.is_file():
        return False, "docs/specs.md does not exist"
    text = p.read_text()
    # Markdown table header row, e.g. "| Spec | ... |" followed by a
    # "|---|...|" separator row.
    has_header = bool(re.search(r"^\|.+\|\s*\n\|[\s:|-]+\|\s*$", text, re.MULTILINE))
    return has_header, f"docs/specs.md header-row regex match = {has_header}"


def _check_adr_template() -> tuple[bool, str]:
    p = REPO / "docs/adr/0000-template.md"
    if not p.is_file():
        return False, "docs/adr/0000-template.md does not exist"
    text = p.read_text()
    required = ["Status", "Supersedes", "Depends on", "Affects",
                "Context", "Decision", "Consequences"]
    missing = [r for r in required if r not in text]
    ok = not missing
    return ok, (f"required sections present = {ok}"
                + (f" (missing: {missing})" if missing else ""))


CLAIMS = [
    ("D1", "docs/adr/ directory exists", lambda: _check_dir("docs/adr")),
    ("D2", "docs/specs/ directory exists", lambda: _check_dir("docs/specs")),
    ("D3", "docs/audits/ directory exists", lambda: _check_dir("docs/audits")),
    ("D4", "docs/specs.md exists and has an index-table header row",
     _check_specs_md_header),
    ("D5", "docs/adr/0000-template.md exists with extended-Nygard sections",
     _check_adr_template),
]


def run() -> dict:
    results = []
    passed = 0
    failed = 0
    for claim_id, claim, check_fn in CLAIMS:
        try:
            ok, evidence = check_fn()
        except Exception as e:
            ok, evidence = False, f"ERROR: {e}"
        results.append({"id": claim_id, "claim": claim,
                         "result": "PASS" if ok else "FAIL",
                         "evidence": evidence})
        if ok:
            passed += 1
        else:
            failed += 1
    verdict = "PASS" if failed == 0 else f"FAIL ({failed} failed)"
    return {"claims": results,
            "summary": {"passed": passed, "failed": failed, "total": len(CLAIMS)},
            "verdict": verdict}


def main() -> int:
    as_json = "--json" in sys.argv
    cert = run()
    if as_json:
        print(json.dumps(cert, indent=2))
        return 0 if cert["verdict"] == "PASS" else 1

    s = cert["summary"]
    print(f"Docs Skeleton Proof — {cert['verdict']}")
    print("=" * 60)
    print(f"Claims: {s['passed']} passed, {s['failed']} failed / {s['total']} total\n")
    for r in cert["claims"]:
        icon = "✓" if r["result"] == "PASS" else "✗"
        print(f"  [{r['id']}] {icon}  {r['claim']}")
        if r["result"] != "PASS":
            print(f"        ↳ {r['evidence']}")
    print(f"\nVerdict: {cert['verdict']}")
    return 0 if cert["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
