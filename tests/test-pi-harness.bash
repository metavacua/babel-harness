#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HARNESS="$REPO_ROOT/bin/pi-harness"
MOCKS="$REPO_ROOT/tests/mocks"
PASS=0; FAIL=0

export PI_BIN="$MOCKS/pi"
export OPENROUTER_CHECK_URL="http://mock-openrouter.invalid"
export OLLAMA_URL="http://mock-ollama.invalid"

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc"
    echo "    expected: $needle"
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

echo "=== pi-harness test suite ==="

echo ""
echo "--- 1: OpenRouter reachable → invokes openrouter provider ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 bash "$HARNESS" --no-cgroup "write a hello function" 2>&1)
rc=$?
assert_exit "exits 0" "0" "$rc"
assert_contains "uses openrouter provider" "provider=openrouter" "$out"

echo ""
echo "--- 2: OpenRouter down, Ollama up → uses ollama ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_OPENROUTER_EXIT=1 MOCK_CURL_OLLAMA_EXIT=0 bash "$HARNESS" --no-cgroup "write a hello function" 2>&1 || true)
assert_contains "uses ollama provider" "provider=ollama" "$out"

echo ""
echo "--- 3: Both providers down → exits 1 ---"
PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 MOCK_OLLAMA_EXIT=1 bash "$HARNESS" --no-cgroup "task" >/dev/null 2>&1
assert_exit "exits 1 when no provider" "1" "$?"

echo ""
echo "--- 4: --status both up ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 MOCK_OLLAMA_EXIT=0 bash "$HARNESS" --status 2>&1)
assert_contains "--status shows reachable" "reachable" "$out"
assert_contains "--status shows running" "running" "$out"

echo ""
echo "--- 5: --status both down ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 MOCK_OLLAMA_EXIT=1 bash "$HARNESS" --status 2>&1)
assert_contains "--status shows unreachable" "unreachable" "$out"
assert_contains "--status shows not running" "not running" "$out"

echo ""
echo "--- 6: --model override forces model ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_OPENROUTER_EXIT=1 MOCK_CURL_OLLAMA_EXIT=0 bash "$HARNESS" --no-cgroup --model "ollama/phi3:mini" "task" 2>&1)
assert_contains "--model override passes phi3:mini" "model=phi3:mini" "$out"

echo ""
echo "--- 7: cgroup enrollment writes a valid (non-stale) PID ---"
# Without --no-cgroup, pi-harness sets up cgroups and enrolls itself.
# The bug: echo "$BASHPID" | sudo tee cgroup.procs evaluates $BASHPID in a
# pipeline subshell that exits before tee runs → "No such process", PID never written.
# The fix: _self=$BASHPID captured before the pipeline.
# This test verifies a PID is actually written (mock sudo + CGROUP_ROOT=tmpdir).
_tmpdir=$(mktemp -d)
mkdir -p "$_tmpdir/cpu/babel-harness" "$_tmpdir/memory/babel-harness"
PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 CGROUP_ROOT="$_tmpdir" \
  bash "$HARNESS" "task" > /dev/null 2>&1 || true
_written_pid=$(cat "$_tmpdir/cpu/babel-harness/cgroup.procs" 2>/dev/null)
if [ -n "$_written_pid" ] && [ "$_written_pid" -gt 1 ] 2>/dev/null; then
  echo "  PASS: cgroup.procs contains valid PID ($_written_pid)"; ((PASS++)) || true
else
  echo "  FAIL: cgroup.procs is empty or contains invalid PID ('$_written_pid')"
  ((FAIL++)) || true
fi
rm -rf "$_tmpdir"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
