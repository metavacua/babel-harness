# Self-Healing babel-local CI (on-error diagnosis) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a babel-harness CI job fails, a *known-working* babel-local model (Ollama `qwen2.5:1.5b`) reads the failing step's log plus the diff and posts a grounded root-cause diagnosis + suggested fix to the run — turning opaque CI failures into actionable analysis, and dogfooding babel-local as a debugging agent (the working model debugging the broken larql path).

**Architecture:** A `diagnose` job gated on `if: failure()` runs after the test jobs, installs Ollama + pulls the proven `qwen2.5:1.5b`, fetches the failed job's log via the GitHub API, and prompts the local model for a one-paragraph diagnosis, written to `$GITHUB_STEP_SUMMARY`. Prerequisite: CI jobs must *actually fail* on error — today `babel-larql-matrix` is falsely green (`set +e` + trailing `echo exit=$?`), so step 1 restores rejection power by moving cell logic to a committed script that exits non-zero on a missing token.

**Tech Stack:** GitHub Actions, Ollama (`qwen2.5:1.5b`), `gh`/GitHub REST API, bash, `jq`/`python3`.

## Global Constraints

- Fork-safe: the diagnosis path uses only a runner-local Ollama model and the read-only `GITHUB_TOKEN`; **no repo secrets**. Diagnosis output goes to `$GITHUB_STEP_SUMMARY` (always writable), not a PR comment (which needs `pull-requests: write`, unavailable on fork PRs).
- Least privilege: `permissions: { contents: read, actions: read }` on the diagnose job (`actions: read` is required to fetch job logs); `contents: read` elsewhere.
- Concurrency: every workflow keeps `concurrency: { group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true }`.
- Known-working model only: the diagnostician is Ollama `qwen2.5:1.5b` (proven usable on a GitHub runner; 100% on both `babel-matrix` roles). Do NOT use larql for diagnosis — it is not yet working in CI.
- Lightest-first: `qwen2.5:1.5b` is the floor that cleared the harder grounded task; do not reach for larger models.

---

### Task 1: Restore rejection power — larql matrix cells report real PASS/FAIL

**Files:**
- Create: `scripts/larql_cell.sh`
- Modify: `.github/workflows/babel-larql-matrix.yml` (replace the inline `case` in the `run cell` step with a call to the script)

**Interfaces:**
- Produces: `scripts/larql_cell.sh` — invoked as `CELL=<name> LARQL_BIN_DIR=<dir> bash scripts/larql_cell.sh`; exits `0` only if the cell's larql invocation succeeds AND its stdout matches the cell's expected marker, else exits non-zero. Consumed by the workflow's test job and (Task 3) referenced by the diagnose job as the kind of failure it explains.

- [ ] **Step 1: Write the failing test** (a local shell test that does not need larql — it exercises the verdict logic via an injected fake command)

```bash
# tests/test-larql-cell.bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }; bad(){ echo "  FAIL: $1: $2"; ((FAIL++))||true; }

# A cell whose command emits the expected marker -> exit 0
CELL=selftest-ok LARQL_FAKE_OUT="the answer is OK" bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && ok "cell with expected marker exits 0" || bad "expected 0" "$?"
# A cell whose command emits nothing/wrong -> exit non-zero
CELL=selftest-ok LARQL_FAKE_OUT="nope" bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && bad "cell without marker should fail" "got 0" || ok "cell missing marker exits non-zero"

echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash tests/test-larql-cell.bash`
Expected: FAIL — `scripts/larql_cell.sh: No such file or directory`.

- [ ] **Step 3: Write minimal implementation**

```bash
# scripts/larql_cell.sh
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# One larql-matrix cell with a REAL verdict: run the cell's larql invocation,
# require its output to contain the expected marker, else exit non-zero. This is
# the rejection power the inline `set +e` case block lacked (green-but-broken).
#
# Env: CELL (required), LARQL_BIN_DIR (dir holding the larql binary; prepended
#      to PATH). Test seam: LARQL_FAKE_OUT bypasses larql and is treated as the
#      command output (for tests/test-larql-cell.bash).
set -uo pipefail
CELL="${CELL:?CELL required}"
[ -n "${LARQL_BIN_DIR:-}" ] && export PATH="$LARQL_BIN_DIR:$PATH"
Q="Reply with exactly one word: OK"

run() {  # echoes the command's combined output; returns its exit code
  if [ -n "${LARQL_FAKE_OUT:-}" ]; then printf '%s' "$LARQL_FAKE_OUT"; return 0; fi
  "$@" 2>&1
}

case "$CELL" in
  selftest-ok)          out="$(run true)";               marker='OK' ;;
  run-hf-granite-q4k)   out="$(run larql run hf://chrishayuk/granite-4.1-3b-q4k-vindex "$Q" -n 8)"; marker='OK' ;;
  run-hf-gemma-f16)     out="$(run larql run hf://chrishayuk/gemma-3-4b-it-vindex "$Q" -n 8)";     marker='OK' ;;
  *) echo "larql_cell: unknown cell: $CELL" >&2; exit 2 ;;
esac
rc=$?
echo "=== cell $CELL output ==="; printf '%s\n' "$out"
if [ "$rc" -ne 0 ] || ! printf '%s' "$out" | grep -qiE "$marker"; then
  echo "=== cell $CELL VERDICT: FAIL (rc=$rc, marker '/$marker/' not found) ==="
  exit 1
fi
echo "=== cell $CELL VERDICT: PASS ==="
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash tests/test-larql-cell.bash`
Expected: PASS — `== 2 passed, 0 failed ==`.

- [ ] **Step 5: Point the workflow at the script**

In `.github/workflows/babel-larql-matrix.yml`, delete `set +e` and the inline `case … esac` from the `run cell` step and replace the step body with:

```yaml
      - name: run cell (streamed, unbuffered)
        run: |
          set -x
          chmod +x bin/larql bin/larql-server || true
          CELL="${{ matrix.cell }}" LARQL_BIN_DIR="$PWD/bin" bash scripts/larql_cell.sh
```

(Drop the `run-extract-*`/`serve-*`/`shannon-*` cells from the `matrix.cell` list for now — they encode an unverified recipe; keep only the `run-hf-*` cells, which now fail *honestly*. The recipe cells return in the separate larql-recipe spec.)

- [ ] **Step 6: Commit**

```bash
git add scripts/larql_cell.sh tests/test-larql-cell.bash .github/workflows/babel-larql-matrix.yml
git commit -m "ci(larql matrix): real per-cell verdicts via scripts/larql_cell.sh (kill false-green)"
```

---

### Task 2: Kill the double-trigger (stop 4–6 runs per push)

**Files:**
- Modify: `.github/workflows/babel-ci.yml` (the `on:` block)

**Interfaces:** none (trigger-config only).

- [ ] **Step 1: Verify the double-run exists**

Run: `gh run list --repo metavacua/babel-harness --branch claude/babel-unified-dispatcher-ci --limit 8 --json event,workflowName --jq '.[] | "\(.event) \(.workflowName)"'`
Expected: `babel-ci` appears for BOTH `push` and `pull_request` on the same commit.

- [ ] **Step 2: Scope the push trigger to main only**

In `.github/workflows/babel-ci.yml`, change:

```yaml
on:
  pull_request:
  push:
    branches: [main, "claude/**"]
  workflow_dispatch:
```
to:
```yaml
on:
  pull_request:
  push:
    branches: [main]   # feature branches are covered by pull_request; avoids the push+PR double-run
  workflow_dispatch:
```

- [ ] **Step 3: Commit and verify one run per workflow on the next push**

```bash
git add .github/workflows/babel-ci.yml
git commit -m "ci: push-trigger babel-ci on main only (feature branches use pull_request) — no double-run"
git push
```
Run: `gh run list --repo metavacua/babel-harness --branch claude/babel-unified-dispatcher-ci --limit 5 --json event,workflowName --jq '.[] | "\(.event) \(.workflowName)"'`
Expected: `babel-ci` appears once (event `pull_request`) for the new commit, not twice.

---

### Task 3: On-error diagnosis job — known-working local model reads the failure and posts a diagnosis

**Files:**
- Create: `scripts/ci_diagnose.sh`
- Modify: `.github/workflows/babel-larql-matrix.yml` (add a `diagnose` job)

**Interfaces:**
- Consumes: `RUN_ID` (env, the failed run's id), `FAILED_JOB` (env, name substring), a running Ollama on `localhost:11434` with `qwen2.5:1.5b` pulled.
- Produces: `scripts/ci_diagnose.sh` — writes a markdown diagnosis to stdout and to `$GITHUB_STEP_SUMMARY` if set.

- [ ] **Step 1: Write the failing test** (verdict/format logic, with the model call faked)

```bash
# tests/test-ci-diagnose.bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }; bad(){ echo "  FAIL: $1"; ((FAIL++))||true; }

out="$(DIAG_FAKE_MODEL='Root cause: OpenBLAS missing. Fix: apt-get install libopenblas-dev.' \
       DIAG_FAKE_LOG='larql: error while loading shared libraries: libopenblas.so.0' \
       bash "$ROOT/scripts/ci_diagnose.sh" 2>/dev/null)"
echo "$out" | grep -qF 'Root cause: OpenBLAS' && ok "diagnosis includes the model's analysis" || bad "analysis missing"
echo "$out" | grep -qiE '## .*diagnos' && ok "output is a markdown section" || bad "no markdown heading"
echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash tests/test-ci-diagnose.bash`
Expected: FAIL — `scripts/ci_diagnose.sh: No such file or directory`.

- [ ] **Step 3: Write minimal implementation**

```bash
# scripts/ci_diagnose.sh
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# On CI failure, ask the KNOWN-WORKING babel-local model (Ollama qwen2.5:1.5b)
# to diagnose it: feed the failed job's log tail (grounded context) and get a
# one-paragraph root-cause + suggested fix. Grounded single-shot — the regime a
# small local model is reliable in. No secrets; output -> markdown/$GITHUB_STEP_SUMMARY.
#
# Env: RUN_ID, FAILED_JOB (used in CI); OLLAMA_URL (default localhost:11434);
#      DIAG_MODEL (default qwen2.5:1.5b). Test seams: DIAG_FAKE_LOG, DIAG_FAKE_MODEL.
set -uo pipefail
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
DIAG_MODEL="${DIAG_MODEL:-qwen2.5:1.5b}"

# 1. gather grounded context: the failed step's log tail (real signal, bounded).
if [ -n "${DIAG_FAKE_LOG:-}" ]; then
  LOG="$DIAG_FAKE_LOG"
else
  LOG="$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}/jobs" \
          --jq ".jobs[] | select(.conclusion==\"failure\") | .id" 2>/dev/null | while read -r jid; do
            gh api "repos/${GITHUB_REPOSITORY}/actions/jobs/${jid}/logs" 2>/dev/null | tail -60
          done | tail -120)"
fi
[ -n "$LOG" ] || LOG="(no failure log retrieved)"

# 2. ask the known-working local model (grounded, bounded).
PROMPT="You are a CI debugger. From ONLY the failing CI log below, state the root cause in one sentence and the specific fix in one sentence. Do not invent details. LOG:
$LOG"
if [ -n "${DIAG_FAKE_MODEL:-}" ]; then
  ANALYSIS="$DIAG_FAKE_MODEL"
else
  ANALYSIS="$(python3 - "$OLLAMA_URL" "$DIAG_MODEL" "$PROMPT" <<'PY'
import json,sys,urllib.request
url,model,prompt=sys.argv[1],sys.argv[2],sys.argv[3]
body=json.dumps({"model":model,"prompt":prompt,"stream":False,"options":{"temperature":0}}).encode()
req=urllib.request.Request(url+"/api/generate",data=body,headers={"Content-Type":"application/json"})
try: print(json.load(urllib.request.urlopen(req,timeout=120)).get("response","(no response)"))
except Exception as e: print(f"(diagnosis model call failed: {e})")
PY
)"
fi

# 3. emit markdown.
REPORT="## babel-local CI diagnosis (qwen2.5:1.5b)
**Failed job:** ${FAILED_JOB:-unknown}

$ANALYSIS

<sub>Diagnosed by the known-working babel-local model on a runner; grounded on the failing log tail.</sub>"
printf '%s\n' "$REPORT"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '%s\n' "$REPORT" >> "$GITHUB_STEP_SUMMARY"
exit 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash tests/test-ci-diagnose.bash`
Expected: PASS — `== 2 passed, 0 failed ==`.

- [ ] **Step 5: Add the `diagnose` job to the workflow**

In `.github/workflows/babel-larql-matrix.yml`, add after the `test` job:

```yaml
  diagnose:
    name: on-error diagnosis (babel-local qwen2.5:1.5b)
    needs: test
    if: failure()
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read      # to fetch the failed job's logs
    steps:
      - uses: actions/checkout@v5
      - name: provision known-working babel-local model (Ollama qwen2.5:1.5b)
        run: |
          curl -fsSL https://ollama.com/install.sh | sh
          nohup ollama serve >/tmp/ollama.log 2>&1 &
          for i in $(seq 1 30); do curl -fsS http://localhost:11434/ >/dev/null 2>&1 && break; sleep 1; done
          ollama pull qwen2.5:1.5b
      - name: diagnose the failure
        env:
          GH_TOKEN: ${{ github.token }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          RUN_ID: ${{ github.run_id }}
          FAILED_JOB: "babel-larql-matrix / test"
        run: bash scripts/ci_diagnose.sh
```

- [ ] **Step 6: Commit and verify end-to-end in CI**

```bash
git add scripts/ci_diagnose.sh tests/test-ci-diagnose.bash .github/workflows/babel-larql-matrix.yml
git commit -m "ci: on-error diagnosis job — babel-local qwen2.5:1.5b explains larql-matrix failures"
git push
```
Verify: the next `babel-larql-matrix` run (whose `run-hf-*` cells now fail honestly, Task 1) triggers the `diagnose` job; read its step summary — it should contain a grounded root-cause paragraph referencing the actual larql error (e.g. the `Io NotFound` / format-incompatibility), produced by the local model.

Run: `gh run view <run-id> --repo metavacua/babel-harness --json jobs --jq '.jobs[] | select(.name|startswith("on-error")) | .conclusion'`
Expected: the diagnose job ran (conclusion `success`), and its summary holds the model's diagnosis.

---

## Out of scope for this plan (next spec: "self-healing — propose & apply fix")

This plan delivers **diagnosis** (the safe, grounded, immediately-buildable rung). The **debug/fix** rungs the directive also names need their own spec because they carry real design and safety decisions that must not be hand-waved:

- **Propose fix** as a unified diff (still read-only; post to summary/PR) — needs a constrained output format and a way to validate the diff applies.
- **Apply fix + re-verify** — a local model editing the working tree and pushing to a PR is an autonomy/safety boundary: guardrails (only on trusted/non-fork PRs, restricted path allow-list, mandatory re-run-must-pass gate, human approval to merge), and the AlphaZero "legal move" checker (a proposed fix is only accepted if the previously-failing cell then passes) belong there.

These build directly on Task 3's diagnosis output and the Task 1 rejection-power gate.

## Self-Review

- **Spec coverage:** on-error → run known-working babel-local model → diagnose ✓ (Task 3). Prerequisite that CI must actually fail ✓ (Task 1 kills false-green). Run-proliferation hygiene ✓ (Task 2). "Debug & fix" explicitly deferred to a named follow-on spec with the design questions enumerated (not a placeholder).
- **Placeholder scan:** every step has runnable code/commands; the two scripts are complete; the model call and the failure-log fetch are concrete.
- **Type consistency:** `scripts/larql_cell.sh` (Task 1) and `scripts/ci_diagnose.sh` (Task 3) are independent scripts with env-based interfaces; the workflow calls match their env contracts (`CELL`/`LARQL_BIN_DIR`; `RUN_ID`/`FAILED_JOB`/`GITHUB_REPOSITORY`).
