#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Acceptance spec for the unified `bin/babel` dispatcher (systematic-debugging
# Phase 4). This is the fabrication-proof arbiter: wrap / replace / wrap+ADR are
# all just candidate implementations that must turn this suite GREEN. Written
# RED-first — bin/babel does not exist yet, so every case fails until built.
#
# Root cause it encodes (see docs/adr/0001-babel-unified-dispatcher.md):
# pi-harness and coding-agent diverge at every boundary. babel unifies:
#   intake (quote-safe), backend selection/override, ONE fallback chain
#   (cloud -> larql smoll, NOT ollama), ONE output envelope, path normalization.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BABEL="$REPO_ROOT/bin/babel"
MOCKS="$REPO_ROOT/tests/mocks"
PASS=0; FAIL=0

pass() { echo "  PASS: $1"; ((PASS++)) || true; }
fail() { echo "  FAIL: $1"; echo "    $2"; ((FAIL++)) || true; }
ok_contains()  { echo "$3" | grep -qFe "$2" && pass "$1" || fail "$1" "expected to contain: $2 | actual: $3"; }
no_contains()  { echo "$3" | grep -qFe "$2" && fail "$1" "expected NOT to contain: $2 | actual: $3" || pass "$1"; }
eq()           { [ "$2" = "$3" ] && pass "$1" || fail "$1" "expected [$2] got [$3]"; }

# Run babel with the full mock harness. $1=extra env (space-sep KEY=VAL), rest=args.
# Captures stdout->__OUT, stderr->__ERR, exit->__RC; fresh MOCK_CALL_LOG->__LOG.
run_babel() {
  local env_extra="$1"; shift
  __LOG="$(mktemp)"
  __OUT="$(env PATH="$MOCKS:$PATH" \
      PI_BIN="$MOCKS/pi" GOOSE_BIN="$MOCKS/goose" LARQL_BIN="$MOCKS/larql" \
      OPENROUTER_CHECK_URL="http://openrouter.test/api/v1/models" \
      LARQL_NO_CGROUP=1 LARQL_NO_DISCOVERY=1 LARQL_PORT=19191 \
      MOCK_CALL_LOG="$__LOG" \
      $env_extra \
      "$BABEL" "$@" 2>/tmp/babel-err.$$)"
  __RC=$?
  __ERR="$(cat /tmp/babel-err.$$ 2>/dev/null)"; rm -f /tmp/babel-err.$$
  __CALLS="$(cat "$__LOG" 2>/dev/null)"; rm -f "$__LOG"
}

echo "== test-babel (acceptance spec for bin/babel) =="

# 1. Exists and runs a task end-to-end when the cloud provider is reachable.
run_babel "MOCK_CURL_OPENROUTER_EXIT=0" "say hello"
eq "babel exists and exits 0 on a reachable cloud task" "0" "$__RC"

# 2. Quote-safe intake — the pi-harness word-drop bug MUST NOT recur.
#    Unquoted multi-word task reaches the backend intact.
run_babel "MOCK_CURL_OPENROUTER_EXIT=0 GOOSE_MODE=auto" --backend goose write a hello function
ok_contains "unquoted multi-word task reaches backend intact" "write a hello function" "$__CALLS$__OUT$__ERR"

# 3. Unified fallback: cloud DOWN -> larql smoll, and specifically NOT ollama.
run_babel "MOCK_CURL_OPENROUTER_EXIT=1" "summarize this"
ok_contains "cloud-down falls back to larql (v1/models health check hit)" "v1/models" "$__CALLS"
no_contains "cloud-down does NOT warm up ollama" "api/generate" "$__CALLS"

# 4. Explicit backend selection routes deterministically.
run_babel "MOCK_CURL_OPENROUTER_EXIT=0" --backend pi "route me to pi"
ok_contains "--backend pi invokes the Pi executor" "MOCK_PI_INVOKED" "$__ERR"

# 5. ONE output contract: a uniform result envelope regardless of backend.
run_babel "MOCK_CURL_OPENROUTER_EXIT=0" --backend pi "envelope please"
ok_contains "emits a uniform babel_result envelope" '"type":"babel_result"' "$__OUT"
ok_contains "envelope names the backend used" '"backend"' "$__OUT"

# 6. Path normalization: relative vindex resolved to an absolute path for larql serve.
run_babel "MOCK_CURL_OPENROUTER_EXIT=1 MOCK_LARQL_RUNNING=0" --backend larql --vindex ./smollm2-360m "x"
ok_contains "relative --vindex is normalized to an absolute path for larql serve" "larql serve /" "$__CALLS"

# 7. Rate-limit is a BUG, not an outcome: a failed/limited backend degrades
#    INTERNALLY to another option and still completes — the caller never sees it.
run_babel "MOCK_CURL_OPENROUTER_EXIT=0 MOCK_PI_EXIT=1" degrade off the failed backend
eq "degradation still exits 0" "0" "$__RC"
ok_contains "degrades off the failed backend and completes" '"ok":true' "$__OUT"
ok_contains "completed on a different (non-failed) backend" '"backend":"goose"' "$__OUT"
no_contains "external result never surfaces a raw rate-limit/error" "Ran into this error" "$__OUT"

# 8. When ALL options are exhausted, babel returns a STRUCTURED fatal envelope,
#    still never a raw provider rate-limit string on the external (stdout) channel.
run_babel "MOCK_CURL_OPENROUTER_EXIT=0 MOCK_GOOSE_RATE_LIMIT=1" --backend goose everything is limited
ok_contains "all-exhausted yields a structured fatal envelope" '"ok":false' "$__OUT"
no_contains "fatal path still hides the raw rate-limit text externally" "Ran into this error" "$__OUT"

# 9. OPT-IN ollama backend: `--backend ollama` routes the Pi agent at a local
#    ollama model (`--provider ollama`). This is the local model babel depends on
#    where ollama is feasible (CI runners), kept OPT-IN so the AUTO chain stays
#    larql-only (test 3) per ADR-0001's "ollama demoted to opt-in".
run_babel "MOCK_CURL_OPENROUTER_EXIT=0 OLLAMA_MODEL=qwen2.5:0.5b" --backend ollama "ask the local model"
ok_contains "--backend ollama drives the Pi agent with provider=ollama" "provider=ollama" "$__ERR"
ok_contains "ollama run reports its model" "model=qwen2.5:0.5b" "$__ERR"
ok_contains "envelope names the ollama backend" '"backend":"ollama"' "$__OUT"

echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
