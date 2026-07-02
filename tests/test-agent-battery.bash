#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOCKS="$REPO_ROOT/tests/mocks"
PASS=0; FAIL=0

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qFe "$needle"; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc (wanted: $needle)"; ((FAIL++)) || true
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

echo "=== agent-battery test suite ==="

echo ""
echo "--- 1: refuses without live server (liveness gate) ---"
out=$(GOOSE_BIN="$MOCKS/goose" LARQL_PORT=59998 \
      bash "$REPO_ROOT/scripts/run_agent_battery.sh" --questions /dev/null 2>&1)
rc=$?
assert_exit "exits 1 when server not alive" "1" "$rc"
assert_contains "error message mentions server not alive" "server not alive" "$out"

echo ""
echo "--- 2: runs with mock server and goose (PASS case) ---"
tmpdir=$(mktemp -d)
questions_file="$tmpdir/questions.txt"
echo "What is the answer to everything?" > "$questions_file"
out=$(PATH="$MOCKS:$PATH" GOOSE_BIN="$MOCKS/goose" LARQL_PORT=59999 OUT_DIR="$tmpdir/out" \
      MOCK_CURL_LARQL_EXIT=0 \
      bash "$REPO_ROOT/scripts/run_agent_battery.sh" --questions "$questions_file" --label test 2>&1)
rc=$?
assert_exit "exits 0 with live server" "0" "$rc"
assert_contains "outputs question count" "done:" "$out"
assert_contains "reports label" "label=test" "$out"
# Verify transcript file was created
if [ -f "$tmpdir/out/transcripts/test-q1.txt" ]; then
  echo "  PASS: transcript file created"; ((PASS++)) || true
else
  echo "  FAIL: transcript file not found at $tmpdir/out/transcripts/test-q1.txt"; ((FAIL++)) || true
fi
rm -rf "$tmpdir"

echo ""
echo "--- 3: GOOSE_RUN_ARGS env seam allows custom goose flags ---"
tmpdir=$(mktemp -d)
questions_file="$tmpdir/questions.txt"
echo "Test question?" > "$questions_file"
calllog="$tmpdir/calllog.txt"
out=$(PATH="$MOCKS:$PATH" GOOSE_BIN="$MOCKS/goose" LARQL_PORT=59999 OUT_DIR="$tmpdir/out" \
      MOCK_CURL_LARQL_EXIT=0 MOCK_CALL_LOG="$calllog" \
      GOOSE_RUN_ARGS="custom --flags --here" \
      bash "$REPO_ROOT/scripts/run_agent_battery.sh" --questions "$questions_file" --label env-test 2>&1)
rc=$?
assert_exit "exits 0 with custom GOOSE_RUN_ARGS" "0" "$rc"
# Mock goose logs its args, verify they include the custom flags
if [ -f "$calllog" ] && grep -q "custom --flags --here" "$calllog"; then
  echo "  PASS: GOOSE_RUN_ARGS env seam works"; ((PASS++)) || true
else
  echo "  FAIL: GOOSE_RUN_ARGS not applied"; ((FAIL++)) || true
fi
rm -rf "$tmpdir"

echo ""
echo "--- 4: --port flag overrides LARQL_PORT ---"
out=$(GOOSE_BIN="$MOCKS/goose" LARQL_PORT=8282 \
      bash "$REPO_ROOT/scripts/run_agent_battery.sh" --questions /dev/null --port 59998 2>&1)
rc=$?
assert_exit "exits 1 when using --port override" "1" "$rc"
assert_contains "uses --port value for health check" "127.0.0.1:59998" "$out"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
