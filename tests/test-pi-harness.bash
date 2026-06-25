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

# All tests use CGROUP_ROOT=$tmpdir so cgroup writes go to a temp directory
# without requiring privilege and without bypassing enforcement logic.
setup_cgroup_tmpdir() {
  local d
  d=$(mktemp -d)
  mkdir -p "$d/cpu/babel-harness" "$d/memory/babel-harness"
  echo "$d"
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qFe "$needle"; then
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
tmpdir=$(setup_cgroup_tmpdir)
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 CGROUP_ROOT="$tmpdir" bash "$HARNESS" "write a hello function" 2>&1)
rc=$?
assert_exit "exits 0" "0" "$rc"
assert_contains "uses openrouter provider" "provider=openrouter" "$out"
rm -rf "$tmpdir"

echo ""
echo "--- 2: OpenRouter down, Ollama up → uses ollama ---"
tmpdir=$(setup_cgroup_tmpdir)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 MOCK_CURL_OLLAMA_EXIT=0 \
  MOCK_OLLAMA_MODEL_LOADED=1 \
  CGROUP_ROOT="$tmpdir" \
  bash "$HARNESS" "write a hello function" 2>&1 || true)
assert_contains "uses ollama provider" "provider=ollama" "$out"
rm -rf "$tmpdir"

echo ""
echo "--- 3: Both providers down → exits 1 before cgroup ---"
# Provider selection fails before cgroup setup; no tmpdir needed.
PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 bash "$HARNESS" "task" >/dev/null 2>&1; _rc=$?
assert_exit "exits 1 when no provider" "1" "$_rc"

echo ""
echo "--- 4: --status both up ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 bash "$HARNESS" --status 2>&1)
assert_contains "--status shows reachable" "reachable" "$out"
assert_contains "--status shows running" "running" "$out"

echo ""
echo "--- 5: --status both down ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 bash "$HARNESS" --status 2>&1)
assert_contains "--status shows unreachable" "unreachable" "$out"
assert_contains "--status shows not running" "not running" "$out"

echo ""
echo "--- 6: --model override forces model ---"
tmpdir=$(setup_cgroup_tmpdir)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 MOCK_CURL_OLLAMA_EXIT=0 \
  MOCK_OLLAMA_MODEL_LOADED=1 MOCK_OLLAMA_MODEL_NAME="phi3:mini" \
  CGROUP_ROOT="$tmpdir" \
  bash "$HARNESS" --model "ollama/phi3:mini" "task" 2>&1)
assert_contains "--model override passes phi3:mini" "model=phi3:mini" "$out"
rm -rf "$tmpdir"

echo ""
echo "--- 7: cgroup setup failure is fatal, not silent ---"
# With a non-writable CGROUP_ROOT, _setup_cgroup fails → harness must exit 1.
tmpdir=$(mktemp -d)
chmod 000 "$tmpdir"
PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 CGROUP_ROOT="$tmpdir" \
  bash "$HARNESS" "task" >/dev/null 2>&1
assert_exit "exits 1 on cgroup failure" "1" "$?"
chmod 755 "$tmpdir"; rm -rf "$tmpdir"

echo ""
echo "--- 8: cgroup enrollment writes a valid (non-stale) PID ---"
tmpdir=$(setup_cgroup_tmpdir)
PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 CGROUP_ROOT="$tmpdir" \
  bash "$HARNESS" "task" > /dev/null 2>&1 || true
_written_pid=$(cat "$tmpdir/cpu/babel-harness/cgroup.procs" 2>/dev/null)
if [ -n "$_written_pid" ] && [ "$_written_pid" -gt 1 ] 2>/dev/null; then
  echo "  PASS: cgroup.procs contains valid PID ($_written_pid)"; ((PASS++)) || true
else
  echo "  FAIL: cgroup.procs is empty or invalid ('$_written_pid')"
  ((FAIL++)) || true
fi
rm -rf "$tmpdir"

echo ""
echo "--- 9: warmup skips api/generate when model already loaded ---"
tmpdir=$(setup_cgroup_tmpdir)
calllog=$(mktemp)
# api/ps already returns the model — no generate should be needed
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 MOCK_CURL_OLLAMA_EXIT=0 \
  MOCK_OLLAMA_MODEL_LOADED=1 MOCK_OLLAMA_MODEL_NAME="qwen2.5-coder:7b" \
  MOCK_CALL_LOG="$calllog" \
  CGROUP_ROOT="$tmpdir" \
  bash "$HARNESS" --model "ollama/qwen2.5-coder:7b" "task" 2>&1 || true)
if grep -q "api/generate" "$calllog"; then
  echo "  FAIL: api/generate was called when model was already loaded"
  ((FAIL++)) || true
else
  echo "  PASS: warmup skipped api/generate (model already hot)"; ((PASS++)) || true
fi
rm -f "$calllog"; rm -rf "$tmpdir"

echo ""
echo "--- 10: warmup calls api/generate when model not loaded ---"
tmpdir=$(setup_cgroup_tmpdir)
calllog=$(mktemp)
counterfile=$(mktemp)
# api/ps returns loaded on 2nd call (after generate triggers the load)
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 MOCK_CURL_OLLAMA_EXIT=0 \
  MOCK_OLLAMA_MODEL_LOADED=0 MOCK_OLLAMA_MODEL_NAME="qwen2.5-coder:7b" \
  MOCK_PS_LOADED_AFTER=2 MOCK_PS_COUNTER_FILE="$counterfile" \
  MOCK_CALL_LOG="$calllog" \
  CGROUP_ROOT="$tmpdir" \
  bash "$HARNESS" --model "ollama/qwen2.5-coder:7b" "task" 2>&1 || true)
if grep -q "api/generate" "$calllog"; then
  echo "  PASS: warmup called api/generate to load model"; ((PASS++)) || true
else
  echo "  FAIL: warmup did not call api/generate when model was not loaded"
  ((FAIL++)) || true
fi
rm -f "$calllog" "$counterfile"; rm -rf "$tmpdir"

echo ""
echo "--- 11: warmup waits for api/ps to confirm model loaded ---"
tmpdir=$(setup_cgroup_tmpdir)
calllog=$(mktemp)
counterfile=$(mktemp)
# api/ps returns loaded only on 3rd call — warmup must poll
out=$(PATH="$MOCKS:$PATH" \
  MOCK_CURL_OPENROUTER_EXIT=1 MOCK_CURL_OLLAMA_EXIT=0 \
  MOCK_OLLAMA_MODEL_LOADED=0 MOCK_OLLAMA_MODEL_NAME="qwen2.5-coder:7b" \
  MOCK_PS_LOADED_AFTER=3 MOCK_PS_COUNTER_FILE="$counterfile" \
  MOCK_CALL_LOG="$calllog" \
  OLLAMA_WARMUP_POLL_INTERVAL=0 \
  CGROUP_ROOT="$tmpdir" \
  bash "$HARNESS" --model "ollama/qwen2.5-coder:7b" "task" 2>&1 || true)
ps_calls=$(grep -c "api/ps" "$calllog" 2>/dev/null || echo 0)
if [ "$ps_calls" -ge 3 ]; then
  echo "  PASS: warmup polled api/ps $ps_calls times until model appeared"; ((PASS++)) || true
else
  echo "  FAIL: warmup made only $ps_calls api/ps call(s); expected >= 3"
  ((FAIL++)) || true
fi
rm -f "$calllog" "$counterfile"; rm -rf "$tmpdir"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
