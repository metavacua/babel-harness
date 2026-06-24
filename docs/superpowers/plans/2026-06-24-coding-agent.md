# Coding Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `bin/coding-agent` — a Goose-backed coding agent that routes tasks to OpenRouter (primary) or `larql serve smollm2-360m` (instant-start local fallback) with a timing demo.

**Architecture:** A bash script checks OpenRouter reachability; if up, invokes Goose with OpenRouter env; if down, ensures `larql serve` is listening at `$LARQL_PORT`, then invokes Goose with `GOOSE_PROVIDER=openai OPENAI_BASE_URL=http://localhost:$LARQL_PORT/v1`. Goose's `developer` extension provides full read/write/bash tool loop. Tests use the same mock-curl pattern as `test-pi-harness.bash`.

**Tech Stack:** bash, Goose v1.36.0, larql v0.1.0 (`$LARQL_BIN`), smollm2-360m vindex (`$LARQL_VINDEX` vindex in the larql cache).

## Global Constraints

- Shell: bash, `set -euo pipefail` on all scripts.
- TDD: every test must be written and verified to **fail** before implementation code is written.
- No modifications to `bin/pi-harness` or `tests/test-pi-harness.bash`.
- All existing tests (14) must still pass after each task.
- `SPDX-License-Identifier: AGPL-3.0-or-later` header on every new script.
- Testable seams: `OPENROUTER_CHECK_URL`, `LARQL_BIN`, `LARQL_PORT`, `LARQL_VINDEX`, `OPENROUTER_MODEL`, `GOOSE_BIN` — all must be overridable via env vars.
- Production defaults: `LARQL_BIN=$HOME/larql/target/release/larql`, `LARQL_PORT=8080`, `LARQL_VINDEX=smollm2-360m`, `OPENROUTER_MODEL=qwen/qwen3-235b-a22b:free`.

---

### Task 1: Add goose and larql mocks

**Files:**
- Create: `tests/mocks/goose`
- Create: `tests/mocks/larql`

**Interfaces:**
- Produces:
  - `MOCK_CALL_LOG` env var path — both mocks append one line per invocation
  - `goose run -t "TASK"` mock: exits 0, writes `{"content":"mock goose response"}` to stdout, records `GOOSE_PROVIDER=$GOOSE_PROVIDER GOOSE_MODEL=$GOOSE_MODEL OPENAI_BASE_URL=$OPENAI_BASE_URL` to call log
  - `larql serve VINDEX --port PORT` mock: exits 0 immediately (simulates background serve), records invocation
  - `larql` called with no args to `serve`: pass-through to health-check handler — when `MOCK_LARQL_RUNNING=1` the `curl` mock returns 200 for the `/v1/models` URL; this is handled by the existing `curl` mock, not `larql` mock

- [ ] **Step 1: Write `tests/mocks/goose`**

```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Mock goose binary — records provider env vars and returns a fixed response.
# Records each invocation to $MOCK_CALL_LOG if set.
if [ -n "${MOCK_CALL_LOG:-}" ]; then
  echo "goose GOOSE_PROVIDER=${GOOSE_PROVIDER:-} GOOSE_MODEL=${GOOSE_MODEL:-} OPENAI_BASE_URL=${OPENAI_BASE_URL:-} args=$*" >> "$MOCK_CALL_LOG"
fi
echo '{"content":"mock goose response"}'
exit "${MOCK_GOOSE_EXIT:-0}"
```

Save to `tests/mocks/goose` and make executable:
```bash
chmod +x tests/mocks/goose
```

- [ ] **Step 2: Write `tests/mocks/larql`**

```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Mock larql binary — records invocations; "serve" exits immediately.
if [ -n "${MOCK_CALL_LOG:-}" ]; then
  echo "larql $*" >> "$MOCK_CALL_LOG"
fi
# For "serve": return immediately (simulate background serve start)
if [ "${1:-}" = "serve" ]; then
  exit 0
fi
exit "${MOCK_LARQL_EXIT:-0}"
```

Save to `tests/mocks/larql` and make executable:
```bash
chmod +x tests/mocks/larql
```

- [ ] **Step 3: Update the existing `tests/mocks/curl` to handle larql health check**

Read `tests/mocks/curl` first, then add this block immediately after the existing openrouter/ollama URL-matching logic:

```bash
# larql /v1/models health check
if echo "$*" | grep -q "v1/models" && ! echo "$*" | grep -q "openrouter"; then
  if [ "${MOCK_CURL_LARQL_EXIT:-${MOCK_CURL_EXIT:-0}}" = "0" ]; then
    echo '{"data":[{"id":"smollm2-360m"}]}'
    exit 0
  else
    exit 1
  fi
fi
```

Insert this block before the final fallthrough at the bottom of `tests/mocks/curl`.

- [ ] **Step 4: Verify existing tests still pass**

```bash
bash tests/test-pi-harness.bash
```

Expected: `=== Results: 14 passed, 0 failed ===`

- [ ] **Step 5: Commit**

```bash
git add tests/mocks/goose tests/mocks/larql tests/mocks/curl
git commit -m "test: add goose and larql mocks, extend curl mock for larql /v1/models"
```

---

### Task 2: Write failing tests for `bin/coding-agent`

**Files:**
- Create: `tests/test-coding-agent.bash`

**Interfaces:**
- Consumes: `tests/mocks/goose`, `tests/mocks/larql`, `tests/mocks/curl`
- Tests are written before `bin/coding-agent` exists — all 4 must fail with "No such file or directory" or similar before Task 3.

- [ ] **Step 1: Write `tests/test-coding-agent.bash`**

```bash
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
```

- [ ] **Step 2: Run tests to verify they ALL fail (bin/coding-agent does not exist yet)**

```bash
bash tests/test-coding-agent.bash 2>&1 || true
```

Expected output: all 4 tests FAIL (agent script not found). If any test passes, the test is wrong — fix the test.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test-coding-agent.bash
git commit -m "test(RED): add 4 failing tests for coding-agent routing"
```

---

### Task 3: Implement `bin/coding-agent`

**Files:**
- Create: `bin/coding-agent`

**Interfaces:**
- Consumes: Goose binary (`$GOOSE_BIN`), larql binary (`$LARQL_BIN`), curl mock
- Produces: exits 0 on success; `coding-agent: provider=<name> model=<model>` on stderr; Goose's native stdout output

- [ ] **Step 1: Write `bin/coding-agent`**

```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

# --- Testable seams ---
OPENROUTER_CHECK_URL="${OPENROUTER_CHECK_URL:-https://openrouter.ai/api/v1/models}"
LARQL_BIN="${LARQL_BIN:-$HOME/larql/target/release/larql}"
LARQL_PORT="${LARQL_PORT:-8080}"
LARQL_VINDEX="${LARQL_VINDEX:-smollm2-360m}"
OPENROUTER_MODEL="${OPENROUTER_MODEL:-qwen/qwen3-235b-a22b:free}"
GOOSE_BIN="${GOOSE_BIN:-goose}"

usage() {
  cat <<'EOF'
Usage: coding-agent [OPTIONS] TASK

Options:
  --model M     Force provider/model (e.g. openrouter/qwen/qwen3-235b-a22b:free
                or larql/smollm2-360m)
  -h, --help    Show this help
EOF
}

_check_openrouter() {
  curl -sf --max-time 3 "$OPENROUTER_CHECK_URL" > /dev/null 2>&1
}

_check_larql() {
  curl -sf --max-time 3 "http://localhost:${LARQL_PORT}/v1/models" > /dev/null 2>&1
}

_start_larql_server() {
  local vindex="$1"
  echo "coding-agent: starting larql serve $vindex on port $LARQL_PORT..." >&2
  "$LARQL_BIN" serve "$vindex" --port "$LARQL_PORT" > /dev/null 2>&1 &
  # larql loads via mmap — typically ready in < 500 ms; poll up to 5 s
  local deadline=$(( $(date +%s) + 5 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if _check_larql; then
      echo "coding-agent: larql serve ready" >&2
      return 0
    fi
    sleep 0.2
  done
  echo "coding-agent: timeout waiting for larql serve to start" >&2
  return 1
}

_run_goose_openrouter() {
  local model="$1" task="$2"
  echo "coding-agent: provider=openrouter model=$model" >&2
  GOOSE_PROVIDER=openrouter GOOSE_MODEL="$model" \
    "$GOOSE_BIN" run -t "$task"
}

_run_goose_larql() {
  local vindex="$1" task="$2"
  echo "coding-agent: provider=larql (openai-compat) model=$vindex" >&2
  GOOSE_PROVIDER=openai \
    OPENAI_BASE_URL="http://localhost:${LARQL_PORT}/v1" \
    OPENAI_API_KEY=larql \
    GOOSE_MODEL="$vindex" \
    "$GOOSE_BIN" run -t "$task"
}

# --- Argument parsing ---
MODEL_OVERRIDE=""
TASK=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL_OVERRIDE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "coding-agent: unknown option: $1" >&2; usage; exit 1 ;;
    *) TASK="$1"; shift ;;
  esac
done

if [ -z "$TASK" ]; then
  echo "coding-agent: TASK argument required" >&2
  usage
  exit 1
fi

# --- Model override: larql/VINDEX forces local path regardless of OpenRouter ---
if [ -n "$MODEL_OVERRIDE" ]; then
  local_provider="${MODEL_OVERRIDE%%/*}"
  local_model="${MODEL_OVERRIDE#*/}"
  if [ "$local_provider" = "larql" ]; then
    if ! _check_larql; then
      _start_larql_server "$local_model" || exit 1
    fi
    _run_goose_larql "$local_model" "$TASK"
    exit $?
  else
    # openrouter/<model> or other explicit override
    _run_goose_openrouter "$local_model" "$TASK"
    exit $?
  fi
fi

# --- Auto-select provider ---
if _check_openrouter; then
  _run_goose_openrouter "$OPENROUTER_MODEL" "$TASK"
else
  local_vindex="$LARQL_VINDEX"
  if ! _check_larql; then
    _start_larql_server "$local_vindex" || exit 1
  fi
  _run_goose_larql "$local_vindex" "$TASK"
fi
```

- [ ] **Step 2: Make executable**

```bash
chmod +x bin/coding-agent
```

- [ ] **Step 3: Run the failing tests and verify they now pass**

```bash
bash tests/test-coding-agent.bash
```

Expected: `=== Results: 4 passed, 0 failed ===`

If any test fails, debug before continuing — do not skip to commit.

- [ ] **Step 4: Verify no regression in pi-harness tests**

```bash
bash tests/test-pi-harness.bash
```

Expected: `=== Results: 14 passed, 0 failed ===`

- [ ] **Step 5: Commit**

```bash
git add bin/coding-agent
git commit -m "feat(GREEN): implement coding-agent — Goose+OpenRouter primary, larql serve fallback"
```

---

### Task 4: Write `bin/demo-coding-agent`

**Files:**
- Create: `bin/demo-coding-agent`

**Interfaces:**
- Consumes: `bin/coding-agent`, real `goose`, real `larql serve`, real OpenRouter
- Produces: stdout timing output showing both provider paths

- [ ] **Step 1: Write `bin/demo-coding-agent`**

```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Demonstrates both provider paths with timing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT="$REPO_ROOT/bin/coding-agent"
TASK="write a Python function that returns the nth Fibonacci number"

echo "=============================================="
echo " coding-agent demo — babel-harness"
echo " $(date)"
echo "=============================================="
echo ""

echo "--- Path 1: OpenRouter (primary) ---"
t0=$(date +%s%N)
"$AGENT" "$TASK" 2>&1
t1=$(date +%s%N)
elapsed=$(( (t1 - t0) / 1000000 ))
echo ""
echo "  elapsed: ${elapsed}ms"
echo ""

echo "--- Path 2: larql serve smollm2-360m (local fallback) ---"
echo "  Forcing larql path (OPENROUTER_CHECK_URL=http://offline.invalid)"
t0=$(date +%s%N)
OPENROUTER_CHECK_URL=http://offline.invalid "$AGENT" "$TASK" 2>&1
t1=$(date +%s%N)
elapsed=$(( (t1 - t0) / 1000000 ))
echo ""
echo "  elapsed: ${elapsed}ms  (larql: <500ms startup, first token <30s for 360M CPU)"
echo ""
echo "=============================================="
```

- [ ] **Step 2: Make executable**

```bash
chmod +x bin/demo-coding-agent
```

- [ ] **Step 3: Run the demo against real services to confirm it works end-to-end**

```bash
bash bin/demo-coding-agent 2>&1
```

Expected:
- Path 1: Goose connects to OpenRouter, returns a Python fibonacci function, elapsed < 30 s.
- Path 2: `coding-agent: starting larql serve smollm2-360m on port 8080...` then `coding-agent: larql serve ready` within ~2 s, Goose connects to localhost:8080, returns output, elapsed < 120 s (360 M CPU decode ~3 tok/s).

If OpenRouter is unreachable during testing, Path 1 will fall through to larql automatically — this is expected behaviour.

- [ ] **Step 4: Commit**

```bash
git add bin/demo-coding-agent
git commit -m "feat: add demo-coding-agent showing both provider paths with timing"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task that implements it |
|---|---|
| `bin/coding-agent` routing script | Task 3 |
| OpenRouter primary path | Task 3 `_run_goose_openrouter` |
| larql serve local fallback | Task 3 `_start_larql_server` + `_run_goose_larql` |
| Goose as agent in both paths | Task 3 (all paths call `$GOOSE_BIN run`) |
| `--model larql/VINDEX` override | Task 3 argument parsing |
| `LARQL_BIN`, `LARQL_PORT`, `LARQL_VINDEX`, `GOOSE_BIN` seams | Task 3 seam declarations |
| 4 tests (open router up, open router down, larql not running, model override) | Task 2 |
| `tests/mocks/goose` records provider env | Task 1 |
| `tests/mocks/larql` exits 0 on `serve` | Task 1 |
| `tests/mocks/curl` handles `/v1/models` | Task 1 |
| No regression in pi-harness 14 tests | Task 3 step 4 |
| `bin/demo-coding-agent` with timing | Task 4 |
| Demo runs end-to-end against real services | Task 4 step 3 |

**Placeholder scan:** None found. All code steps contain complete bash.

**Type consistency:** All env var names (`MOCK_CURL_LARQL_EXIT`, `MOCK_LARQL_RUNNING`, `MOCK_CALL_LOG`, `GOOSE_BIN`, `LARQL_BIN`, `LARQL_PORT`, `LARQL_VINDEX`) are used consistently across Tasks 1, 2, and 3.

**Gap found:** The curl mock update in Task 1 Step 3 says "insert before the final fallthrough" but the existing curl mock needs to be read first to find the right insertion point. Annotated in the step accordingly.

**Gap found:** `sleep 0.2` in `_start_larql_server` requires `sleep` to accept fractional seconds (GNU coreutils). On this Linux machine that is guaranteed. Noted.
