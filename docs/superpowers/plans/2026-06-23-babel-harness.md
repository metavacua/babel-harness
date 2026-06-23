# Babel Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `pi-harness` — a resource-aware bash script that routes coding tasks to Pi agent via OpenRouter free-tier (default) or local Ollama (fallback), with Claude Code as full operator.

**Architecture:** A single bash script (`bin/pi-harness`) with testable seams (overridable env vars for URLs and binaries). Tests use mock binaries in `tests/mocks/` prepended to PATH. Cgroup v1 memory limiting written directly to `/sys/fs/cgroup/memory/` without external tools.

**Tech Stack:** bash, Pi agent (`~/.local/share/pi-node/current/bin/pi`), Ollama v0.23.2, cgroup v1, `curl`, `systemctl`

## Global Constraints

- Cgroup v1 hierarchy only — kernel 6.6, `/sys/fs/cgroup/memory/` (NOT cgroupv2 `/sys/fs/cgroup/`)
- `User=metavacua` in ollama.service (not `ollama` — that user does not exist)
- Pi binary: `~/.local/share/pi-node/current/bin/pi`
- Local model: `qwen2.5-coder:7b` (primary, replaces `deepseek-coder:6.7b`)
- Non-tool local model: `phi3:mini` (retained for non-tool queries only)
- Memory ceiling: 5368709120 bytes (5 GB)
- Repo root: wherever `babel-harness` was cloned; harness symlinked to `~/.local/bin/pi-harness`
- SPDX header on all scripts: `AGPL-3.0-or-later`
- NOPASSWD sudo available for `systemctl` and `/sys/fs/cgroup/` writes

---

## File Map

| Path | Action | Purpose |
|------|--------|---------|
| `/etc/systemd/system/ollama.service` | Modify | Change `User=ollama` → `User=metavacua` |
| `~/.pi/agent/settings.json` | Modify | Add `ollama/qwen2.5-coder:7b`, remove `ollama/deepseek-coder:6.7b` |
| `bin/pi-harness` | Create | Main harness script |
| `tests/test-pi-harness.bash` | Create | Shell test suite |
| `tests/mocks/curl` | Create | Mock curl binary for tests |
| `tests/mocks/pi` | Create | Mock pi binary for tests |
| `tests/mocks/ollama` | Create | Mock ollama binary for tests |
| `tests/mocks/systemctl` | Create | Mock systemctl binary for tests |
| `~/.local/bin/pi-harness` | Symlink | Make harness available on PATH |
| `~/.claude/CLAUDE.md` | Modify | Add delegation heuristic |

---

## Task 1: Fix Ollama service

**Files:**
- Modify: `/etc/systemd/system/ollama.service`

**Interfaces:**
- Produces: `ollama list` exits 0 and Ollama serves on `http://localhost:11434`

- [ ] **Step 1: Edit the service file**

```bash
sudo sed -i 's/^User=ollama$/User=metavacua/' /etc/systemd/system/ollama.service
```

Verify the change:
```bash
grep "^User=" /etc/systemd/system/ollama.service
# Expected: User=metavacua
```

- [ ] **Step 2: Reload and restart**

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
sleep 3
sudo systemctl status ollama --no-pager | head -5
```

Expected: `Active: active (running)`

- [ ] **Step 3: Verify Ollama is serving**

```bash
curl -sf http://localhost:11434 && echo "Ollama OK" || echo "Ollama not responding"
ollama list
```

Expected: `ollama list` prints a table (may be empty — that's fine).

- [ ] **Step 4: Commit**

```bash
cd $(git rev-parse --show-toplevel)
git add -A
git commit -m "ops: fix Ollama service to run as metavacua user"
```

---

## Task 2: Pull qwen2.5-coder:7b and update Pi settings

**Files:**
- Modify: `~/.pi/agent/settings.json`

**Interfaces:**
- Consumes: running Ollama from Task 1
- Produces: `ollama run qwen2.5-coder:7b` succeeds; Pi settings list `ollama/qwen2.5-coder:7b`

- [ ] **Step 1: Pull the model**

```bash
ollama pull qwen2.5-coder:7b
```

Expected: download progress then `pull complete`. Takes several minutes — the model is ~4.5 GB.

- [ ] **Step 2: Verify tool-call support**

```bash
ollama run qwen2.5-coder:7b --nowordwrap "Reply with exactly: TOOLS_OK"
```

Expected output contains `TOOLS_OK`. If Ollama errors with "tool" in the message, note it but continue — the Pi invocation test in Task 4 is the definitive check.

- [ ] **Step 3: Update Pi's enabledModels**

Open `~/.pi/agent/settings.json`. Find the `enabledModels` array. Make these changes:
- Add `"ollama/qwen2.5-coder:7b"` if not present
- Remove `"ollama/deepseek-coder:6.7b"` (causes tool-call errors)
- Keep `"ollama/phi3:mini"` (non-tool fallback)

The array should contain at minimum:
```json
"enabledModels": [
  "openrouter/openrouter/free",
  "openrouter/auto",
  "openrouter/~anthropic/claude-haiku-latest",
  "ollama/qwen2.5-coder:7b",
  "ollama/phi3:mini"
]
```

- [ ] **Step 4: Verify Pi sees the model**

```bash
~/.local/share/pi-node/current/bin/pi --provider ollama --model qwen2.5-coder:7b --print --no-tools "Say: READY"
```

Expected: output contains `READY`. If Pi errors, check `settings.json` syntax with `python3 -m json.tool ~/.pi/agent/settings.json`.

- [ ] **Step 5: Commit**

```bash
cd $(git rev-parse --show-toplevel)
git add -A
git commit -m "config: pull qwen2.5-coder:7b, update Pi enabledModels"
```

---

## Task 3: Write failing tests for pi-harness

**Files:**
- Create: `tests/mocks/curl`
- Create: `tests/mocks/pi`
- Create: `tests/mocks/ollama`
- Create: `tests/mocks/systemctl`
- Create: `tests/test-pi-harness.bash`

**Interfaces:**
- Consumes: nothing (bin/pi-harness does not exist yet — tests must fail)
- Produces: `bash tests/test-pi-harness.bash` exits non-zero with "command not found" or similar

- [ ] **Step 1: Create mock binaries**

`tests/mocks/curl`:
```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Behaviour controlled by MOCK_CURL_EXIT env var (default 0 = success)
exit "${MOCK_CURL_EXIT:-0}"
```

`tests/mocks/pi`:
```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Records invocation args; emits minimal JSON; behaviour via MOCK_PI_EXIT
echo "MOCK_PI_INVOKED provider=$(echo "$@" | grep -o '\-\-provider [^ ]*' | awk '{print $2}') model=$(echo "$@" | grep -o '\-\-model [^ ]*' | awk '{print $2}')" >&2
echo '{"role":"assistant","content":"MOCK_RESPONSE"}'
exit "${MOCK_PI_EXIT:-0}"
```

`tests/mocks/ollama`:
```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
exit "${MOCK_OLLAMA_EXIT:-0}"
```

`tests/mocks/systemctl`:
```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
echo "MOCK_SYSTEMCTL: $*" >&2
exit "${MOCK_SYSTEMCTL_EXIT:-0}"
```

Make all mocks executable:
```bash
chmod +x tests/mocks/curl tests/mocks/pi tests/mocks/ollama tests/mocks/systemctl
```

- [ ] **Step 2: Create test suite**

`tests/test-pi-harness.bash`:
```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HARNESS="$REPO_ROOT/bin/pi-harness"
MOCKS="$REPO_ROOT/tests/mocks"
PASS=0; FAIL=0

# Override PI_BIN and URLs via env vars (testable seams)
export PI_BIN="$MOCKS/pi"
export OPENROUTER_CHECK_URL="http://mock-openrouter.invalid"
export OLLAMA_URL="http://mock-ollama.invalid"

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc"
    echo "    expected to contain: $needle"
    echo "    actual: $haystack"
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

# --- Test 1: OpenRouter reachable → invokes pi with openrouter provider ---
echo ""
echo "--- 1: OpenRouter reachable path ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 bash "$HARNESS" "write a hello function" 2>&1)
exit_code=$?
assert_exit "exits 0" "0" "$exit_code"
assert_contains "invokes openrouter provider" "provider=openrouter" "$out"

# --- Test 2: OpenRouter unreachable, Ollama healthy → uses ollama ---
echo ""
echo "--- 2: OpenRouter down, Ollama up ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 MOCK_OLLAMA_EXIT=0 bash "$HARNESS" "write a hello function" 2>&1 || true)
assert_contains "invokes ollama provider" "provider=ollama" "$out"

# --- Test 3: Both down → exits non-zero ---
echo ""
echo "--- 3: Both providers down ---"
PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 MOCK_OLLAMA_EXIT=1 bash "$HARNESS" "task" > /dev/null 2>&1
assert_exit "exits non-zero when no provider" "1" "$?"

# --- Test 4: --status with both up ---
echo ""
echo "--- 4: --status both up ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=0 MOCK_OLLAMA_EXIT=0 bash "$HARNESS" --status 2>&1)
assert_contains "--status reports OpenRouter reachable" "reachable" "$out"
assert_contains "--status reports Ollama running" "running" "$out"

# --- Test 5: --status with both down ---
echo ""
echo "--- 5: --status both down ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 MOCK_OLLAMA_EXIT=1 bash "$HARNESS" --status 2>&1)
assert_contains "--status reports OpenRouter unreachable" "unreachable" "$out"
assert_contains "--status reports Ollama not running" "not running" "$out"

# --- Test 6: --model override forces provider ---
echo ""
echo "--- 6: --model override ---"
out=$(PATH="$MOCKS:$PATH" MOCK_CURL_EXIT=1 bash "$HARNESS" --model "ollama/phi3:mini" "task" 2>&1)
assert_contains "--model override uses phi3:mini" "model=phi3:mini" "$out"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
cd $(git rev-parse --show-toplevel)
bash tests/test-pi-harness.bash
```

Expected: errors like `bash: /path/to/bin/pi-harness: No such file or directory`. All 6 tests fail.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/
git commit -m "test: add failing pi-harness test suite with mocks"
```

---

## Task 4: Implement pi-harness

**Files:**
- Create: `bin/pi-harness`
- Create: `~/.local/bin/pi-harness` (symlink)

**Interfaces:**
- Consumes: `PI_BIN`, `OPENROUTER_CHECK_URL`, `OLLAMA_URL` env vars (testable seams)
- Produces: delegated task result as JSON to stdout; provider/model selection to stderr

- [ ] **Step 1: Write the script**

`bin/pi-harness`:
```bash
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

# --- Testable seams (override in tests or environment) ---
PI_BIN="${PI_BIN:-$(command -v pi 2>/dev/null || echo "$HOME/.local/share/pi-node/current/bin/pi")}"
OPENROUTER_CHECK_URL="${OPENROUTER_CHECK_URL:-https://openrouter.ai/api/v1/models}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

# --- Defaults ---
OPENROUTER_MODEL="${OPENROUTER_MODEL:-openrouter/free}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5-coder:7b}"
CGROUP_NAME="babel-harness"
CGROUP_MEMORY_BYTES=5368709120  # 5 GB

usage() {
  cat <<'EOF'
Usage: pi-harness [OPTIONS] TASK

Options:
  --status        Report OpenRouter and Ollama health
  --repair        Attempt to fix Ollama (restart service)
  --model M       Override model, format: provider/model
  --no-cgroup     Skip cgroup enforcement
  -h, --help      Show this help
EOF
}

_check_openrouter() {
  curl -sf --max-time 3 "$OPENROUTER_CHECK_URL" > /dev/null 2>&1
}

_check_ollama() {
  curl -sf --max-time 3 "$OLLAMA_URL" > /dev/null 2>&1
}

_repair_ollama() {
  echo "pi-harness: restarting Ollama..." >&2
  sudo systemctl restart ollama >&2 2>&1
  sleep 2
  _check_ollama
}

_setup_cgroup() {
  local cg_mem="/sys/fs/cgroup/memory/$CGROUP_NAME"
  if [ ! -d "$cg_mem" ]; then
    sudo mkdir -p "$cg_mem" 2>/dev/null || return 1
  fi
  echo "$CGROUP_MEMORY_BYTES" | sudo tee "$cg_mem/memory.limit_in_bytes" > /dev/null 2>&1 || return 1
  echo "0" | sudo tee "$cg_mem/memory.swappiness" > /dev/null 2>&1 || true
  return 0
}

_add_to_cgroup() {
  local pid="$1"
  echo "$pid" | sudo tee "/sys/fs/cgroup/memory/$CGROUP_NAME/cgroup.procs" > /dev/null 2>&1
}

cmd_status() {
  if _check_openrouter; then
    echo "OpenRouter: reachable"
  else
    echo "OpenRouter: unreachable"
  fi
  if _check_ollama; then
    echo "Ollama:     running"
  else
    echo "Ollama:     not running"
  fi
}

cmd_repair() {
  if _repair_ollama; then
    echo "pi-harness: Ollama is now running" >&2
    return 0
  else
    echo "pi-harness: Ollama repair failed" >&2
    return 1
  fi
}

_select_provider_model() {
  if _check_openrouter; then
    echo "openrouter $OPENROUTER_MODEL"
  elif _check_ollama; then
    echo "ollama $OLLAMA_MODEL"
  elif _repair_ollama; then
    echo "ollama $OLLAMA_MODEL"
  else
    echo "none none"
  fi
}

cmd_run() {
  local task="$1"
  local model_override="${2:-}"
  local no_cgroup="${3:-0}"

  local provider model
  if [ -n "$model_override" ]; then
    provider="${model_override%%/*}"
    model="${model_override#*/}"
  else
    read -r provider model < <(_select_provider_model)
    if [ "$provider" = "none" ]; then
      echo '{"error":"no provider available — OpenRouter unreachable and Ollama failed to start"}' >&2
      return 1
    fi
  fi

  echo "pi-harness: provider=$provider model=$model" >&2

  if [ "$no_cgroup" = "0" ] && _setup_cgroup; then
    # Run pi in a subshell, add that subshell to the cgroup
    (
      _add_to_cgroup "$$"
      exec "$PI_BIN" --provider "$provider" --model "$model" --print --mode json "$task"
    )
  else
    [ "$no_cgroup" = "0" ] && echo "pi-harness: cgroup setup failed, proceeding without limit" >&2
    "$PI_BIN" --provider "$provider" --model "$model" --print --mode json "$task"
  fi
}

# --- Argument parsing ---
MODEL_OVERRIDE=""
NO_CGROUP=0
TASK=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status)    cmd_status; exit 0 ;;
    --repair)    cmd_repair; exit $? ;;
    --model)     MODEL_OVERRIDE="$2"; shift 2 ;;
    --no-cgroup) NO_CGROUP=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "pi-harness: unknown option: $1" >&2; usage; exit 1 ;;
    *)           TASK="$1"; shift ;;
  esac
done

if [ -z "$TASK" ]; then
  echo "pi-harness: TASK argument required" >&2
  usage
  exit 1
fi

cmd_run "$TASK" "$MODEL_OVERRIDE" "$NO_CGROUP"
```

- [ ] **Step 2: Make executable and symlink**

```bash
chmod +x bin/pi-harness
ln -sf "$(pwd)/bin/pi-harness" "$HOME/.local/bin/pi-harness"
```

Verify:
```bash
which pi-harness
pi-harness --help
```

Expected: prints usage.

- [ ] **Step 3: Run tests — verify they pass**

```bash
bash tests/test-pi-harness.bash
```

Expected: `Results: 6 passed, 0 failed`

- [ ] **Step 4: Commit**

```bash
git add bin/ tests/
git commit -m "feat: implement pi-harness with OpenRouter/Ollama routing and cgroup enforcement"
```

---

## Task 5: Add delegation heuristic to CLAUDE.md

**Files:**
- Modify: `~/.claude/CLAUDE.md` (create if absent)

**Interfaces:**
- Produces: Claude Code knows when to call `pi-harness` vs handle inline

- [ ] **Step 1: Check if ~/.claude/CLAUDE.md exists**

```bash
ls -la ~/.claude/CLAUDE.md 2>/dev/null || echo "does not exist"
```

- [ ] **Step 2: Append the delegation heuristic**

If `~/.claude/CLAUDE.md` does not exist, create it. Append this section (do not replace existing content):

```markdown
## Babel Harness — Delegation to Pi Agent

When working on coding tasks, delegate to `pi-harness` for:
- Mechanical, well-defined, in-repo edits (renaming, refactoring, boilerplate)
- Tasks that must not leave the machine (offline mode)
- Parallel drafts or exploratory branches while main reasoning continues here

Handle inline (do not delegate) for:
- Architecture decisions or novel reasoning
- Tasks requiring multi-turn conversation history
- Anything where the task description is ambiguous — clarify first

### How to delegate

```bash
pi-harness "TASK DESCRIPTION"          # auto-selects provider
pi-harness --status                    # check provider health before delegating
pi-harness --model ollama/phi3:mini "TASK"  # force local lightweight model
```

Output is JSON with a `content` field containing the result.
When the result includes file edits, apply them using the Edit tool.
```

- [ ] **Step 3: Verify**

```bash
grep -A3 "Babel Harness" ~/.claude/CLAUDE.md
```

Expected: the heuristic section is present.

- [ ] **Step 4: Commit**

```bash
cd $(git rev-parse --show-toplevel)
git add -A
git commit -m "docs: add CLAUDE.md delegation heuristic for pi-harness"
```

---

## Task 6: Push to GitHub and demonstrate

**Files:**
- Modify: `docs/specs/2026-06-23-design.md` (no change needed — already committed)

**Interfaces:**
- Produces: live demo output showing OpenRouter path and `--status`

- [ ] **Step 1: Push all commits**

```bash
cd $(git rev-parse --show-toplevel)
git push origin main
```

- [ ] **Step 2: Run --status**

```bash
pi-harness --status
```

Expected:
```
OpenRouter: reachable
Ollama:     running
```
(Ollama may show "not running" if the model hasn't been loaded yet — that's OK.)

- [ ] **Step 3: Run a real coding task via OpenRouter**

```bash
pi-harness "Write a bash function called greet that takes a name argument and prints Hello, NAME!"
```

Expected: JSON output with `content` containing a bash function definition. Also shows `pi-harness: provider=openrouter model=openrouter/free` on stderr.

- [ ] **Step 4: Test local fallback manually**

```bash
pi-harness --model "ollama/qwen2.5-coder:7b" "Write a bash function called add that takes two numbers and prints their sum"
```

Expected: JSON output. May take 10–30 seconds on first call as the model loads into RAM. Watch memory: `free -h` in another terminal should show ~4.5 GB used.

- [ ] **Step 5: Commit demonstration output to docs**

```bash
pi-harness --status > docs/demo-status.txt 2>&1
pi-harness "Write a bash function called greet that takes a name and prints Hello, NAME!" > docs/demo-openrouter.json 2>&1 || true
git add docs/
git commit -m "docs: add demonstration output for pi-harness"
git push origin main
```
