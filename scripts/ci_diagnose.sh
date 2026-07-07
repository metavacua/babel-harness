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
HERE="$(cd "$(dirname "$0")" && pwd)"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
DIAG_MODEL="${DIAG_MODEL:-qwen2.5:1.5b}"

# 1. gather grounded context: the failed step's log tail (real signal, bounded).
if [ -n "${DIAG_FAKE_LOG:-}" ]; then
  LOG="$DIAG_FAKE_LOG"
else
  LOG="$(bash "$HERE/gh_fail_log.sh")"
fi
[ -n "$LOG" ] || LOG="(no failure log retrieved)"
# persist for the fix step so it reuses this log instead of re-fetching (one fetch/run).
[ -n "${GITHUB_WORKSPACE:-}" ] && printf '%s' "$LOG" > "$GITHUB_WORKSPACE/ci-fail-log.txt"

# 2. ask the known-working local model (grounded, bounded).
PROMPT="You are a CI debugger. From ONLY the failing CI log below, state the root cause in one sentence and the specific fix in one sentence. Do not invent details. LOG:
$LOG"
if [ -n "${DIAG_FAKE_MODEL:-}" ]; then
  ANALYSIS="$DIAG_FAKE_MODEL"
else
  ANALYSIS="$(python3 "$HERE/ollama_generate.py" "$OLLAMA_URL" "$DIAG_MODEL" "$PROMPT" 120 "(no response)")"
fi

# 3. emit markdown.
REPORT="## babel-local CI diagnosis (qwen2.5:1.5b)
**Failed job:** ${FAILED_JOB:-unknown}

$ANALYSIS

<sub>Diagnosed by the known-working babel-local model on a runner; grounded on the failing log tail.</sub>"
printf '%s\n' "$REPORT"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '%s\n' "$REPORT" >> "$GITHUB_STEP_SUMMARY"
exit 0
