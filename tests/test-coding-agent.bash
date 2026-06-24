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
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc"
    echo "    expected to contain: $needle"
    echo "    actual:   $haystack"
    ((FAIL++)) || true
  fi
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
# MOCK_CURL_LARQL_EXIT=1 first call fails (not running), then 0 after serve starts
# We simulate this by making larql respond to serve and the second curl check pass
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 \
  MOCK_CURL_LARQL_EXIT=0 \
  MOCK_LARQL_RUNNING=0 \
  MOCK_CALL_LOG="$calllog" \
  GOOSE_BIN="$MOCKS/goose" \
  LARQL_BIN="$MOCKS/larql" \
  bash "$AGENT" "write a hello function" 2>&1 || true)
assert_contains "larql serve invoked" "larql serve" "$(cat "$calllog")"
rm -f "$calllog"

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
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
