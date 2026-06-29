#!/usr/bin/env bash
# Smoke driver for babel-harness.
# Run from the repo root: bash .claude/skills/run-babel-harness/smoke.sh
# Requires: OPENROUTER_API_KEY set (or Ollama running locally).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
export PATH="$REPO/bin:$PATH"
cd "$REPO"

PASS=0; FAIL=0
ok()   { echo "  OK  $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL $1: $2"; FAIL=$((FAIL+1)); }

echo "=== babel-harness smoke ==="
echo "    repo: $REPO"
echo ""

# ── 1. Health check ──────────────────────────────────────────────────────────
STATUS=$(pi-harness --status 2>&1)
if echo "$STATUS" | grep -qE "OpenRouter: reachable|Ollama: running"; then
  ok "pi-harness --status  [$(echo "$STATUS" | tr '\n' ' ' | sed 's/  */ /g')]"
else
  fail "pi-harness --status" "$STATUS"
fi

# ── 2. pi-harness task round-trip ────────────────────────────────────────────
# Output: JSONL stream; agent_end.messages[-1].content[].text has the reply.
# Write to tmpfile to avoid pipeline hang with heredoc in command substitution.
_PIOUT=$(mktemp)
pi-harness "output exactly the word SMOKEPASS and nothing else" > "$_PIOUT" 2>/dev/null
REPLY=$(python3 - "$_PIOUT" <<'PYEOF'
import sys, json, pathlib
data = pathlib.Path(sys.argv[1]).read_text()
for line in data.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
        if obj.get("type") == "agent_end":
            for msg in reversed(obj.get("messages", [])):
                for b in msg.get("content", []):
                    if b.get("type") == "text":
                        print(b["text"].strip())
                        sys.exit(0)
    except Exception:
        pass
PYEOF
)
rm -f "$_PIOUT"
if echo "$REPLY" | grep -q "SMOKEPASS"; then
  ok "pi-harness task → '$REPLY'"
else
  fail "pi-harness task" "expected SMOKEPASS, got: '$REPLY'"
fi

# ── 3. coding-agent test suite ────────────────────────────────────────────────
SUITE=$(bash tests/test-coding-agent.bash 2>&1)
RESULT=$(echo "$SUITE" | grep "Results:" | tail -1)
if echo "$RESULT" | grep -q "0 failed"; then
  ok "test-coding-agent.bash  [$RESULT]"
else
  FAILURES=$(echo "$SUITE" | grep "  FAIL:" | head -5)
  fail "test-coding-agent.bash" "$RESULT  |  $FAILURES"
fi

# ── 4. DDT composition proof ──────────────────────────────────────────────────
PROOF=$(python3 scripts/ddt_proof.py 2>&1)
if echo "$PROOF" | grep -q "Verdict: PROVEN"; then
  ok "ddt_proof.py  [Verdict: PROVEN]"
else
  fail "ddt_proof.py" "$(echo "$PROOF" | grep Verdict:)"
fi

# ── 5. larql-graft integration tests ─────────────────────────────────────────
GRAFT=$(bash tests/test-larql-graft.bash 2>&1)
GRAFT_RESULT=$(echo "$GRAFT" | grep "Results:" | tail -1)
if echo "$GRAFT_RESULT" | grep -q "0 failed"; then
  ok "test-larql-graft.bash  [$GRAFT_RESULT]"
else
  fail "test-larql-graft.bash" "$(echo "$GRAFT" | grep '  FAIL:')"
fi

# ── 6. larql serve (if larql-server built) ───────────────────────────────────
# Requires a full vindex with attn_weights.bin (not a browse-level slice).
# Default: smollm2-360m.vindex — the full vindex available locally.
# The babel-harness coding-agent vindex is a gate/down-only slice; larql-server
# needs attn_weights.bin + tokenizer files which only the full vindex carries.
LARQL_BIN="${LARQL_BIN:-$HOME/larql/target/release/larql}"
LARQL_SERVER="${LARQL_SERVER:-$HOME/larql/target/release/larql-server}"
LARQL_VINDEX="${LARQL_VINDEX:-$HOME/larql-vindexes/smollm2-360m.vindex}"
if [ -x "$LARQL_BIN" ] && [ -x "$LARQL_SERVER" ] && [ -d "$LARQL_VINDEX" ]; then
  export LARQL_PORT="${LARQL_PORT:-8282}"
  # start server in background, kill on exit
  "$LARQL_BIN" serve "$LARQL_VINDEX" --port "$LARQL_PORT" >/dev/null 2>&1 &
  _SRV=$!
  trap "kill $_SRV 2>/dev/null; wait $_SRV 2>/dev/null || true" EXIT
  # poll up to 60s (smollm2-360m weight pre-load takes ~10-15s)
  _RDY=0
  for _i in $(seq 1 60); do
    if curl -sf "http://localhost:${LARQL_PORT}/v1/models" >/dev/null 2>&1; then
      _RDY=1; break
    fi
    sleep 1
  done
  if [ "$_RDY" = "1" ]; then
    ok "larql serve  [port $LARQL_PORT, vindex: $(basename "$LARQL_VINDEX")]"
    # quick gate-KNN query via coding-agent larql path (model name = vindex basename)
    _VNAME=$(basename "$LARQL_VINDEX" .vindex)
    # B8 fix: capture exit code — a panic makes Goose exit non-zero (HTTP 500 from server).
    # Previously this grep matched "provider=larql" which appears before inference, masking B7.
    _CA_EXIT=0
    _CA_OUT=$(LARQL_PORT="$LARQL_PORT" \
      coding-agent --model "larql/$_VNAME" \
      "say hello" 2>&1 | tail -5) || _CA_EXIT=$?
    if [ "$_CA_EXIT" -eq 0 ]; then
      ok "coding-agent --model larql/* → inference succeeded"
    else
      fail "coding-agent larql path" "inference failed (exit $_CA_EXIT): $_CA_OUT"
    fi
  else
    fail "larql serve" "server did not start within 30s"
  fi
else
  echo "  SKIP larql serve  [set LARQL_BIN/LARQL_SERVER/LARQL_VINDEX to enable]"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
