#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT="$REPO_ROOT/bin/coding-agent"
MOCKS="$REPO_ROOT/tests/mocks"
PASS=0; FAIL=0

export OPENROUTER_CHECK_URL="http://mock-openrouter.invalid"
export LARQL_PORT="19191"   # high port; nothing runs there

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qFe "$needle"; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc"
    echo "    expected to contain: $needle"
    echo "    actual:   $haystack"
    ((FAIL++)) || true
  fi
}

assert_pass() {
  local desc="$1"
  echo "  PASS: $desc"; ((PASS++)) || true
}

assert_fail() {
  local desc="$1" reason="$2"
  echo "  FAIL: $desc"
  echo "    reason: $reason"
  ((FAIL++)) || true
}

assert_exit() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc (exit $actual)"; ((PASS++)) || true
  else
    echo "  FAIL: $desc (expected exit $expected, got $actual)"; ((FAIL++)) || true
  fi
}

echo "=== coding-agent test suite ==="

echo ""
echo "--- 1: OpenRouter reachable → Goose uses openrouter provider ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_EXIT=0 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 0" "0" "$rc"
assert_contains "goose called with openrouter provider" "GOOSE_PROVIDER=openrouter" "$(cat "$calllog")"
rm -f "$calllog"

echo ""
echo "--- 2: OpenRouter down → larql serve started, Goose uses openai+base_url ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_CURL_LARQL_EXIT=0 \
  MOCK_LARQL_RUNNING=1 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 0" "0" "$rc"
assert_contains "goose called with openai provider" "GOOSE_PROVIDER=openai" "$(cat "$calllog")"
assert_contains "goose called with larql base url" "OPENAI_BASE_URL=http://localhost:${LARQL_PORT}/v1" "$(cat "$calllog")"
rm -f "$calllog"

echo ""
echo "--- 3: larql not running → coding-agent starts larql serve ---"
calllog=$(mktemp)
counterfile=$(mktemp)
# /v1/models: call 1 fails (count=1 < 2), call 2 succeeds (count=2 >= 2)
# This simulates: initial check fails → agent starts larql serve → poll passes
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_LARQL_RUNNING_AFTER=2 \
  MOCK_LARQL_COUNTER_FILE="$counterfile" \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1 || true)
assert_contains "larql serve invoked" "larql serve" "$(cat "$calllog")"
rm -f "$calllog" "$counterfile"

echo ""
echo "--- 4: --model larql/smollm2-360m forces larql path even if OpenRouter up ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_EXIT=0 \
  MOCK_LARQL_RUNNING=1 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" --model "larql/smollm2-360m" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 0" "0" "$rc"
assert_contains "goose uses openai provider (forced larql)" "GOOSE_PROVIDER=openai" "$(cat "$calllog")"
rm -f "$calllog"

echo ""
echo "--- 5: GOOSE_MODE=auto is always set (headless safe) ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_EXIT=0 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1)
assert_contains "GOOSE_MODE=auto set for openrouter path" "GOOSE_MODE=auto" "$(cat "$calllog")"
rm -f "$calllog"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_CURL_LARQL_EXIT=0 \
  MOCK_LARQL_RUNNING=1 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1)
assert_contains "GOOSE_MODE=auto set for larql path" "GOOSE_MODE=auto" "$(cat "$calllog")"
rm -f "$calllog"

echo ""
echo "--- 6: --model openrouter/MODEL passes stripped model to openrouter path ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_EXIT=0 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" --model "openrouter/qwen/qwen3-235b-a22b:free" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 0" "0" "$rc"
assert_contains "openrouter override uses openrouter provider" "GOOSE_PROVIDER=openrouter" "$(cat "$calllog")"
assert_contains "openrouter override strips prefix from model name" "GOOSE_MODEL=qwen/qwen3-235b-a22b:free" "$(cat "$calllog")"
rm -f "$calllog"

echo ""
echo "--- 7: larql-server log path is printed to stderr (issue #1) ---"
logfile=$(mktemp)
calllog=$(mktemp)
counterfile=$(mktemp)
# Initial larql check fails, _start_larql_server is called (prints log path), poll then passes
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_LARQL_RUNNING_AFTER=2 \
  MOCK_LARQL_COUNTER_FILE="$counterfile" \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  LARQL_LOG_FILE="$logfile" \
  bash "$AGENT" "write a hello function" 2>&1 || true)
assert_contains "stderr reports larql-server log path" "larql-server log: $logfile" "$out"
rm -f "$calllog" "$logfile" "$counterfile"

echo ""
echo "--- 8: LARQL_INFERENCE_TIMEOUT seam controls goose call timeout (issue #3) ---"
calllog=$(mktemp)
# A 1s timeout with a mock goose that sleeps 2s should cause exit 124
PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_CURL_LARQL_EXIT=0 \
  MOCK_LARQL_RUNNING=1 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  MOCK_GOOSE_SLEEP=2 \
  LARQL_INFERENCE_TIMEOUT=1 \
  bash "$AGENT" "write a hello function" > /dev/null 2>&1
rc=$?
assert_exit "times out when goose exceeds LARQL_INFERENCE_TIMEOUT" "124" "$rc"
rm -f "$calllog"

echo ""
echo "--- 9: larql-server subprocess PID is killed when coding-agent exits (issue #2) ---"
pid_file=$(mktemp)
counter_file=$(mktemp)
calllog=$(mktemp)
# Mock larql: forks sleep 9999 (writes PID), then exits immediately — same as real larql.
# LARQL_SERVER_FINDER tells coding-agent how to find the subprocess PID (in production:
# ss -tlpn; in tests: read from the file the mock wrote).
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_LARQL_RUNNING_AFTER=2 \
  MOCK_LARQL_COUNTER_FILE="$counter_file" \
  MOCK_CALL_LOG="$calllog" \
  MOCK_LARQL_SERVE_PID_FILE="$pid_file" \
  LARQL_SERVER_FINDER="cat $pid_file" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1 || true)
server_pid=$(cat "$pid_file" 2>/dev/null || echo "")
if [ -n "$server_pid" ] && kill -0 "$server_pid" 2>/dev/null; then
  kill "$server_pid" 2>/dev/null || true
  assert_fail "larql-server subprocess PID killed on coding-agent exit" "PID $server_pid still alive after exit"
else
  assert_pass "larql-server subprocess PID killed on coding-agent exit"
fi
rm -f "$calllog" "$pid_file" "$counter_file"

echo ""
echo "--- 10: startup timeout exits 1 with message when health check never passes ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_CURL_LARQL_EXIT=1 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  LARQL_START_TIMEOUT=1 \
  bash "$AGENT" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 1 on startup timeout" "1" "$rc"
assert_contains "emits timeout message" "timeout waiting for larql serve to start" "$out"
rm -f "$calllog"

echo ""
echo "--- 11: launcher exits prematurely → fast-fail with log-file hint (issue #8) ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_CURL_LARQL_EXIT=1 \
  MOCK_LARQL_SERVE_EXIT=1 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  LARQL_START_TIMEOUT=5 \
  bash "$AGENT" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 1 on premature launcher exit" "1" "$rc"
assert_contains "emits premature-exit message with log hint" "exited prematurely" "$out"
rm -f "$calllog"

echo ""
echo "--- 12: cgroup enrollment skipped silently when cgroup path does not exist ---"
pid_file=$(mktemp)
counter_file=$(mktemp)
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_LARQL_RUNNING_AFTER=2 \
  MOCK_LARQL_COUNTER_FILE="$counter_file" \
  MOCK_CALL_LOG="$calllog" \
  MOCK_LARQL_SERVE_PID_FILE="$pid_file" \
  LARQL_SERVER_FINDER="cat $pid_file" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  CGROUP_ROOT="/nonexistent/cgroup" \
  bash "$AGENT" "write a hello function" 2>&1 || true)
if echo "$out" | grep -qFe "enrolled"; then
  assert_fail "no enrolled message when cgroup path does not exist" "output contains 'enrolled' even though cgroup path /nonexistent/cgroup does not exist"
else
  assert_pass "no enrolled message when cgroup path does not exist"
fi
rm -f "$calllog" "$pid_file" "$counter_file"

echo ""
echo "--- 13: --model without value exits 1 with helpful message ---"
out=$(bash "$AGENT" --model 2>&1)
rc=$?
assert_exit "exits 1 on missing --model value" "1" "$rc"
assert_contains "emits helpful --model value error" "--model requires a value" "$out"

echo ""
echo "--- 14: goose exits 0 on rate limit → coding-agent exits 1 (issue #7) ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_EXIT=0 \
  MOCK_GOOSE_RATE_LIMIT=1 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 1 when goose rate-limits (masked exit 0)" "1" "$rc"
assert_contains "emits error message hinting at rate limit" "Ran into this error" "$out"
rm -f "$calllog"

echo ""
echo "--- 15: -- sentinel ends option parsing; remaining args become TASK ---"
calllog=$(mktemp)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_EXIT=0 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" -- --this-looks-like-an-option 2>&1 || true)
if echo "$out" | grep -qFe "unknown option"; then
  assert_fail "-- is not treated as an unknown option" "output contains 'unknown option' instead of passing -- as end-of-options"
else
  assert_pass "-- ends option parsing (no unknown-option error)"
fi
rm -f "$calllog"

echo ""
echo "--- 16: --model openrouter/ with empty model name exits 1 with helpful message ---"
out=$(bash "$AGENT" --model "openrouter/" "write a function" 2>&1)
rc=$?
assert_exit "exits 1 on --model openrouter/ empty model" "1" "$rc"
assert_contains "emits helpful empty-model error" "model name" "$out"

echo ""
echo "--- 17: --model larql/ with empty vindex name exits 1 with helpful message ---"
out=$(bash "$AGENT" --model "larql/" "write a function" 2>&1)
rc=$?
assert_exit "exits 1 on --model larql/ empty vindex" "1" "$rc"
assert_contains "emits helpful empty-vindex error" "model name" "$out"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
