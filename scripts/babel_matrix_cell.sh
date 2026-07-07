#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# babel_matrix_cell.sh — ONE strategy-matrix cell. Runs a grounded ROLE task on
# a runner-local MODEL via BACKEND, N times, grades each answer against the
# role's golden oracle, and emits quantified reliability (pass/N, rate, mean ms).
# This is the CI embodiment of the decomposition thesis: bounded grounded
# single-shot tasks, the regime the cheap substrate is reliable in.
#
# Always exits 0 — it MEASURES, it does not gate. The numbers are the product.
#
# Env: MODEL, BACKEND, ROLE (required); MATRIX_N (default 5); OLLAMA_URL.
# Test seam: BABEL_MATRIX_FAKE_RESPONSE bypasses the model and returns that text.
set -uo pipefail

MODEL="${MODEL:?MODEL required}"
BACKEND="${BACKEND:?BACKEND required}"
ROLE="${ROLE:?ROLE required}"
N="${MATRIX_N:-5}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

# --- Roles: a grounded, closed-form prompt + the regex its CORRECT answer matches ---
case "$ROLE" in
  bug-detect)   # dogfoods the real #18 bug pattern
    PROMPT='In this bash line the task variable is set as: TASK=$1 . If a task has several words, this line keeps only the first word and silently drops the rest. Is that a bug? Answer with exactly one word: YES or NO.'
    ORACLE='YES|BUG' ;;
  path-guard)   # grounded path judgment
    PROMPT='A pull request modifies the file at path ".github/workflows/deploy.yml". Is that path located inside the ".github" directory? Answer with exactly one word: YES or NO.'
    ORACLE='YES' ;;
  *) echo "babel_matrix_cell: unknown role: $ROLE" >&2; exit 2 ;;
esac

# --- Backend: run PROMPT, print the model's answer text on stdout ---
run_backend() {
  if [ -n "${BABEL_MATRIX_FAKE_RESPONSE:-}" ]; then
    printf '%s' "$BABEL_MATRIX_FAKE_RESPONSE"; return 0
  fi
  case "$BACKEND" in
    ollama-direct)
      MODEL="$MODEL" PROMPT="$PROMPT" OLLAMA_URL="$OLLAMA_URL" python3 - <<'PY'
import json, os, urllib.request
body = json.dumps({"model": os.environ["MODEL"], "prompt": os.environ["PROMPT"],
                   "stream": False, "options": {"temperature": 0}}).encode()
req = urllib.request.Request(os.environ["OLLAMA_URL"] + "/api/generate",
                             data=body, headers={"Content-Type": "application/json"})
try:
    print(json.load(urllib.request.urlopen(req, timeout=120)).get("response", ""))
except Exception as e:
    print(f"__BACKEND_ERROR__ {e}")
PY
      ;;
    goose|pi|nca)   # roadmap backends — matrix records the coverage gap honestly
      echo "__BACKEND_NOT_WIRED__ ($BACKEND)" ;;
    *) echo "__UNKNOWN_BACKEND__ ($BACKEND)" ;;
  esac
}

pass=0; fail=0; sum_ms=0
for i in $(seq 1 "$N"); do
  t0=$(date +%s%3N)
  ans="$(run_backend)"
  t1=$(date +%s%3N)
  dt=$(( t1 - t0 )); sum_ms=$(( sum_ms + dt ))
  if printf '%s' "$ans" | grep -qiE "$ORACLE"; then pass=$((pass+1)); v=PASS; else fail=$((fail+1)); v=FAIL; fi
  printf '  [%s/%s/%s] trial %d: %-4s %dms | %s\n' \
    "$MODEL" "$BACKEND" "$ROLE" "$i" "$v" "$dt" "$(printf '%s' "$ans" | tr '\n' ' ' | head -c 48)" >&2
done

rate=$(awk "BEGIN{printf \"%.0f\", 100*$pass/$N}")
mean=$(( sum_ms / N ))
json=$(printf '{"model":"%s","backend":"%s","role":"%s","n":%d,"pass":%d,"fail":%d,"rate_pct":%s,"mean_ms":%d}' \
        "$MODEL" "$BACKEND" "$ROLE" "$N" "$pass" "$fail" "$rate" "$mean")
echo "$json"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### cell \`$MODEL / $BACKEND / $ROLE\`"
    echo "- reliability: **$pass/$N** ($rate%) | mean latency ${mean}ms"
    echo "- \`$json\`"
  } >> "$GITHUB_STEP_SUMMARY"
fi
exit 0
