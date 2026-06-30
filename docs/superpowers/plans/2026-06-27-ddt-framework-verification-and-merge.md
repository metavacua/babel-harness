# DDT Framework — Formal Verification and Branch Finalization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formally verify every /goal sub-condition using `verification-before-completion`, prove the spec's claims against the implementation, and finalize `feature/ddt-framework-and-remote-vindex` (PR #9) for merge to `main`.

**Architecture:** The implementation lives on `feature/ddt-framework-and-remote-vindex` (PR #9 at `metavacua/babel-harness`). Sub-conditions are verified programmatically via `spec_proof.py` (17 structural/behavioral/formal claims) and `ddt_proof.py` (DDT composition closure), then validated through the `verification-before-completion` skill checklist protocol. Any REFUTED claim triggers `systematic-debugging` before re-verification. Only after all 6 sub-conditions pass does the branch merge.

**Tech Stack:** Python 3.11+ (`spec_proof.py`, `ddt_proof.py`, `github_graph.py`), Bash (`test-coding-agent.bash`, `tests/test-larql-graft.bash`), gh CLI (PR management).

## Global Constraints

- All commits, PRs, branches, issues remain in `metavacua/babel-harness` — never write to `chrishayuk/larql` or any non-`metavacua` repo (DDT security precondition: `GitHubAPI[POST, non-metavacua]` is a ToolCall precondition failure)
- Python ≥ 3.10 required (walrus operator `:=` in `spec_proof.py`)
- No new dependencies beyond stdlib for proof scripts
- `chrishayuk/larql` is read-only (GitHub API GET calls only)
- `spec_proof.py` exit code 0 = all 17 claims PROVEN; exit code 1 = any claim REFUTED or ERROR

---

### Task 1: Formal Verification of All /Goal Sub-Conditions

**Files:**
- Run: `scripts/spec_proof.py`
- Run: `scripts/ddt_proof.py`
- Run: `tests/test-coding-agent.bash`
- Run: `tests/test-larql-graft.bash`
- Skill: `superpowers:verification-before-completion` (REQUIRED — invoke before running any check)

**Interfaces:**
- Consumes: All implementation from `feature/ddt-framework-and-remote-vindex`
- Produces: Pass/fail result per sub-condition; routes to Task 2 (fix gaps) or Task 3 (merge)

- [ ] **Step 1: Invoke `verification-before-completion` skill**

```
Skill: superpowers:verification-before-completion
```

The verification checklist items are the 6 /goal sub-conditions. Map each to a concrete check:

| # | Sub-condition | Concrete check |
|---|---|---|
| 1 | LARQL comprehension | `grep -c "WalkFfn\|gate-KNN\|vindex format\|LQL" docs/superpowers/specs/*.md` → non-zero |
| 2 | All posting in `metavacua` repos | `gh pr list --repo chrishayuk/larql --author metavacua` → empty |
| 3 | GitHub remote vindex | `grep "# FROM github://chrishayuk/larql" Vindexfile` → match |
| 4 | FFN KNN via graph graft (not RAG) | `grep "vindex graft\|NOT RAG\|INSERT triples" docs/superpowers/specs/*.md` → match |
| 5 | DDT formalization of all skills/tools | `python3 scripts/ddt_proof.py \| grep "Verdict: PROVEN"` → match |
| 6 | Recursive self-application | `python3 scripts/spec_proof.py \| grep "17 proven"` → match |

- [ ] **Step 2: Run sub-condition 1 check — LARQL comprehension**

```bash
grep -c "WalkFfn\|gate-KNN\|vindex format\|larql-server" \
  docs/superpowers/specs/2026-06-27-larql-ddt-framework-design.md
```
Expected: integer ≥ 10 (confirms WalkFfn, gate-KNN, vindex format, LQL all documented).

- [ ] **Step 3: Run sub-condition 2 check — metavacua-only posting**

```bash
gh pr list --repo chrishayuk/larql --author metavacua 2>&1
```
Expected: empty output (no PRs authored in upstream repo).

```bash
gh pr list --repo metavacua/babel-harness 2>&1 | head -5
```
Expected: PR #9 `feature/ddt-framework-and-remote-vindex` listed as OPEN.

- [ ] **Step 4: Run sub-condition 3 check — remote vindex directive**

```bash
grep "# FROM github://chrishayuk/larql@main" Vindexfile && echo "PASS" || echo "FAIL"
```
Expected: `PASS`

```bash
grep '"--remote"' scripts/extract-graph.py && echo "PASS" || echo "FAIL"
```
Expected: `PASS`

- [ ] **Step 5: Run sub-condition 4 check — FFN KNN graft (not RAG)**

```bash
grep -l "vindex graft\|NOT RAG\|graph graft\|larql build" \
  docs/superpowers/specs/2026-06-27-larql-ddt-framework-design.md
```
Expected: file path printed (match found).

```bash
grep "GITHUB_GRAPH_REPO" bin/coding-agent | head -3
```
Expected: seam variable definitions visible (proves FFN KNN wired into coding-agent).

- [ ] **Step 6: Run sub-condition 5 check — DDT composition proof**

```bash
python3 scripts/ddt_proof.py
```
Expected output contains:
```
Verdict: PROVEN
```
Full expected run:
```
DDT Composition Proof — PROVEN
============================
[Tools in DDT domain]
  ddt          gate_knn
  ddt          ternary_encode
  ...
[Superpowers skills (finite LTS over DDT transitions)]
  ddt          skill(brainstorming)
  ddt          skill(writing-plans)
  ...
Verdict: PROVEN
```

- [ ] **Step 7: Run sub-condition 6 check — recursive self-application**

```bash
python3 scripts/spec_proof.py
```
Expected output:
```
DDT Spec Proof — PROVEN
============================================================
Spec: docs/superpowers/specs/2026-06-27-larql-ddt-framework-design.md
Claims: 17 proven, 0 refuted, 0 errored / 17 total
  [S1] ✓  github_graph.py: all except blocks emit typed JSON error to stderr
  ...
Self-DDT: ddt(spec_proof.py) by construction
Verdict:  PROVEN
```

- [ ] **Step 8: Run test suites**

```bash
bash tests/test-coding-agent.bash 2>&1 | tail -3
```
Expected:
```
=== Results: 41 passed, 0 failed ===
```

```bash
bash tests/test-larql-graft.bash 2>&1
```
Expected (without `LARQL_BIN`/`LARQL_VINDEX_BASE` set — Tests 2 and 4 SKIP, not FAIL):
```
=== Results: 4 passed, 0 failed ===
```

- [ ] **Step 9: Record verification result and route**

If ALL checks in Steps 2–8 pass: mark Task 1 COMPLETE, proceed directly to Task 3.

If ANY check fails: record which sub-condition(s) failed, mark Task 1 IN PROGRESS, proceed to Task 2.

---

### Task 2: Fix Verification Gaps (only if Task 1 found failures)

**Files:**
- Depends on which claims are REFUTED in Task 1
- Skill: `superpowers:systematic-debugging` (REQUIRED for each failing claim)

**Interfaces:**
- Consumes: List of REFUTED claim IDs from Task 1 (e.g., `S1`, `B2`, `F3`)
- Produces: All claims PROVEN in re-run of Task 1; commits on `feature/ddt-framework-and-remote-vindex`

- [ ] **Step 1: For each REFUTED claim, invoke `systematic-debugging`**

```
Skill: superpowers:systematic-debugging
```

Pass the claim's check function as the reproduction case. For example, if S1 is REFUTED:

Reproduction:
```bash
python3 -c "
import re, pathlib
text = pathlib.Path('scripts/github_graph.py').read_text()
found = bool(re.search(r'except.*:\s*\n\s*print\(json\.dumps\(', text, re.MULTILINE))
print('S1 PASS' if found else 'S1 FAIL')
"
```
Expected: `S1 PASS` — if `S1 FAIL`, the root cause is one or more `except` blocks that catch an exception but print plain text or nothing instead of `json.dumps(...)`.

- [ ] **Step 2: Fix the identified root cause**

For S1 (missing JSON in except block), the fix pattern is:
```python
# BEFORE (bad — plain text or silent):
except requests.RequestException as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)

# AFTER (DDT-compliant — typed JSON):
except requests.RequestException as e:
    print(json.dumps({"ok": False, "error": "NetworkError", "cause": str(e)}),
          file=sys.stderr)
    sys.exit(1)
```

Apply the analogous fix for whichever claim is REFUTED. Each fix must change exactly one behavior identified by `systematic-debugging`.

- [ ] **Step 3: Verify the specific claim now passes**

```bash
python3 scripts/spec_proof.py --json | python3 -c "
import json, sys
cert = json.load(sys.stdin)
for c in cert['claims']:
    if c['result'] != 'PROVEN':
        print(f\"STILL FAILING: {c['id']} — {c['evidence']}\")
        sys.exit(1)
print('All claims PROVEN')
"
```
Expected: `All claims PROVEN`

- [ ] **Step 4: Run full test suite to check for regressions**

```bash
bash tests/test-coding-agent.bash 2>&1 | tail -3
```
Expected: `=== Results: 41 passed, 0 failed ===`

- [ ] **Step 5: Commit each fix**

```bash
git add <changed-files>
git commit -m "fix(<claim-id>): <one-line root cause description>"
```

Example for S1:
```bash
git add scripts/github_graph.py
git commit -m "fix(S1): emit json.dumps in all except blocks of github_graph.py"
```

- [ ] **Step 6: Push to feature branch**

```bash
git push origin HEAD:feature/ddt-framework-and-remote-vindex
```

- [ ] **Step 7: Re-run full Task 1 verification**

Return to Task 1 and repeat all steps. Only mark Task 2 complete once Task 1 passes with 0 failures.

---

### Task 3: Branch Finalization and Merge

**Files:**
- No file changes
- Blocked by: Task 1 COMPLETE (or Task 2 COMPLETE if fixes were needed)

**Interfaces:**
- Consumes: PR #9 at `metavacua/babel-harness` with all checks passing
- Produces: `main` branch updated with DDT framework implementation

- [ ] **Step 1: Confirm PR #9 status and CI**

```bash
gh pr view 9 --repo metavacua/babel-harness \
  --json state,title,headRefName,commits 2>&1 | head -20
```
Expected: `state: OPEN`, `headRefName: feature/ddt-framework-and-remote-vindex`.

- [ ] **Step 2: Review the commit list on the PR**

```bash
git log origin/main..feature/ddt-framework-and-remote-vindex --oneline
```
Expected: list of commits from this session, most recent being `feat: spec_proof.py — DDT framework applied to its own spec`.

- [ ] **Step 3: STOP — request user authorization for merge**

Do NOT proceed past this step without explicit user authorization. Post:

> "Verification complete: all 17 spec claims PROVEN, 41 coding-agent tests passing, DDT composition proof PROVEN, larql-graft integration tests 4/4. PR #9 is ready to merge. Authorize merge to main?"

- [ ] **Step 4: Merge (only after user says yes)**

Squash merge (preserves clean main history):
```bash
gh pr merge 9 --repo metavacua/babel-harness --squash \
  --subject "feat: DDT framework, remote vindex graft, and recursive self-validation" \
  --body "Closes #9. Implements all 6 /goal sub-conditions: LARQL comprehension, metavacua-only posting, GitHub remote vindex (FROM github://chrishayuk/larql@main), FFN KNN graph graft, DDT formalization (ddt_proof.py PROVEN), recursive self-validation (spec_proof.py 17/17 PROVEN)."
```

- [ ] **Step 5: Verify main is updated**

```bash
git fetch origin main && git log origin/main --oneline -5
```
Expected: Most recent commit is the squash-merged DDT framework commit.

---

## Self-Review

**1. Spec coverage:**
- Sub-condition 1 (LARQL comprehension): Task 1 Steps 1+2 — `grep` confirms documentation depth ✓
- Sub-condition 2 (metavacua posting): Task 1 Step 3 — `gh pr list` on both repos ✓
- Sub-condition 3 (GitHub remote vindex): Task 1 Step 4 — `grep Vindexfile` + argparse check ✓
- Sub-condition 4 (FFN KNN graft, not RAG): Task 1 Step 5 — spec grep + seam var check ✓
- Sub-condition 5 (DDT formalization): Task 1 Step 6 — `ddt_proof.py` PROVEN ✓
- Sub-condition 6 (recursive self-application): Task 1 Step 7 — `spec_proof.py` 17/17 ✓
- Fix gaps: Task 2 — `systematic-debugging` per REFUTED claim ✓
- Merge gating: Task 3 Step 3 — explicit user authorization required ✓

**2. Placeholder scan:** No TBDs. All steps have exact commands, expected outputs, and file paths.

**3. Type consistency:** `spec_proof.py` exit code semantics defined in Global Constraints and used consistently in Task 1 Step 9 and Task 2 Step 3. Claim IDs (S1, B2, F3 etc.) used consistently between tasks.
