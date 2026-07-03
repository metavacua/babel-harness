#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Self-contained goose demo runner: chat-path ablation control (N4).
#
# Runs the SAME question battery through goose twice against ONE port,
# swapping only whether the backing chat_shim APPLYs the certified patch
# chain (scripts/pipeline/chat_shim.py --patches-dir) or not (--no-patches,
# the ablation twin) -- the chat-path analogue of scripts/pipeline/matrix.py's
# evaluate_pair/patch_prelude split for browse/infer cells. A question is
# PASS(attribution=agent-vindex) only if goose's answer contains the
# expected target when the shim is patched AND does NOT contain it when the
# shim is unpatched -- same "confounded"/"absent" vocabulary as
# scripts.pipeline.matrix.evaluate_pair.
#
# Process-lifecycle invariants (see the module's git history / SDD report
# for why these are load-bearing, not stylistic):
#   1. Each chat_shim's PID is captured explicitly at launch (`... & SHIM_PID=$!`).
#      It is stopped ONLY via `kill "$SHIM_PID"` + `wait "$SHIM_PID"` --
#      NEVER by pattern (pkill -f / kill $(pgrep -f ...) against this
#      script's own argv would self-match and self-destruct, since this
#      script's own command line contains the same substrings it would be
#      grepping for).
#   2. `cleanup` (EXIT/INT/TERM trap) kills whichever shim is CURRENTLY
#      tracked in $SHIM_PID, guarding for it being unset/already-reaped --
#      idempotent, safe to invoke more than once.
#   3. Before EVERY shim launch, the target port is checked free (`ss -tln`);
#      a busy port fails loudly rather than silently testing a stale build.
#   4. Every launch is health-gated: poll /v1/models up to
#      $SHIM_HEALTH_TIMEOUT_S seconds, bailing out immediately (with the
#      shim's own log tail) the moment the shim process itself has already
#      exited -- never wait out the full timeout against a dead process.
#
# Usage:
#   scripts/run_goose_demo.sh --bin $LARQL_BIN \
#     --vindex ~/work/vindexes/smollm2-360m-canonical.vindex \
#     --patches-dir ~/work/artifacts/induction \
#     --questions questions.txt --expected expected.txt \
#     --out ~/work/artifacts/goose-demo --port 8282
#
# Regenerating questions.txt / expected.txt (DOCUMENTATION ONLY -- this
# script never runs generation or any live-model command itself; run this
# one-liner yourself, separately, from the repo root, AFTER
# scripts/run_induction.py has produced certificates.jsonl):
#
#   python3 -c '
#   import json
#   from pathlib import Path
#   from scripts.pipeline.lql_session import canonical_prompt
#   cert_path = Path.home() / "work/artifacts/induction/certificates.jsonl"
#   certs = [json.loads(l) for l in cert_path.read_text().splitlines() if l.strip()]
#   qs, exp = [], []
#   for c in certs:
#       if not c.get("I_n_ok"):
#           break  # stop-at-first-gap: see scripts/pipeline/matrix.py:certified_patches
#       qs.append(canonical_prompt(c["edge"]["s"], c["edge"]["r"]))
#       exp.append(c["edge"]["o"])
#   Path("questions.txt").write_text("\n".join(qs) + "\n")
#   Path("expected.txt").write_text("\n".join(exp) + "\n")
#   '
#
# Environment seams:
#   CHAT_SHIM_CMD — command (word-split) that launches the OpenAI-compat
#                   chat shim (default: "python3 -u scripts/pipeline/chat_shim.py")
#   BATTERY_SCRIPT — path to run_agent_battery.sh (default: sibling script)
#   GOOSE_BIN / GOOSE_RUN_ARGS — forwarded, unmodified, to run_agent_battery.sh
#   SHIM_HEALTH_TIMEOUT_S — health-gate poll budget in seconds (default: 30)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CHAT_SHIM_CMD="${CHAT_SHIM_CMD:-python3 -u scripts/pipeline/chat_shim.py}"
BATTERY_SCRIPT="${BATTERY_SCRIPT:-$SCRIPT_DIR/run_agent_battery.sh}"
SHIM_HEALTH_TIMEOUT_S="${SHIM_HEALTH_TIMEOUT_S:-30}"

BIN=""
VINDEX=""
PATCHES_DIR=""
QUESTIONS=""
EXPECTED=""
OUT=""
PORT=8282

while [ $# -gt 0 ]; do
  case "$1" in
    --bin) BIN="$2"; shift 2 ;;
    --vindex) VINDEX="$2"; shift 2 ;;
    --patches-dir) PATCHES_DIR="$2"; shift 2 ;;
    --questions) QUESTIONS="$2"; shift 2 ;;
    --expected) EXPECTED="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

for pair in "--bin:$BIN" "--vindex:$VINDEX" "--patches-dir:$PATCHES_DIR" \
            "--questions:$QUESTIONS" "--expected:$EXPECTED" "--out:$OUT"; do
  name="${pair%%:*}"; val="${pair#*:}"
  if [ -z "$val" ]; then
    echo "missing required arg: $name" >&2
    exit 2
  fi
done

mkdir -p "$OUT/transcripts" "$OUT/logs"

# -- process lifecycle: single tracked PID, reused across the two passes --
SHIM_PID=""

cleanup() {
  if [ -n "${SHIM_PID:-}" ] && kill -0 "$SHIM_PID" 2>/dev/null; then
    kill "$SHIM_PID" 2>/dev/null || true
    wait "$SHIM_PID" 2>/dev/null || true
  fi
  SHIM_PID=""
}
trap cleanup EXIT INT TERM

check_port_free() {
  local port="$1"
  if ss -tlnp 2>/dev/null | grep -qE ":${port}([[:space:]]|\$)"; then
    echo "port ${port} busy — refusing to start (stale shim?)" >&2
    exit 1
  fi
}

# Poll /v1/models up to $SHIM_HEALTH_TIMEOUT_S seconds. Bails out the moment
# the shim process itself has already exited -- never waits out the full
# timeout against a process that is already dead (requirement 4).
health_gate() {
  local port="$1" pid="$2" log="$3"
  local waited=0
  while [ "$waited" -lt "$SHIM_HEALTH_TIMEOUT_S" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "chat_shim (pid $pid) exited before becoming healthy" >&2
      echo "--- shim log tail ($log) ---" >&2
      tail -n 40 "$log" >&2 2>/dev/null || true
      exit 1
    fi
    if curl -sf --max-time 2 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "chat_shim on :${port} did not become healthy within ${SHIM_HEALTH_TIMEOUT_S}s" >&2
  echo "--- shim log tail ($log) ---" >&2
  tail -n 40 "$log" >&2 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
  exit 1
}

# run_pass LABEL EXTRA_ARGS... : start the shim with EXTRA_ARGS, health-gate
# it, run the agent battery under LABEL, then stop the shim by its
# explicitly-captured PID (never by pattern -- see module docstring).
run_pass() {
  local label="$1"; shift
  local log="$OUT/logs/${label}-shim.log"

  check_port_free "$PORT"

  # shellcheck disable=SC2086 -- CHAT_SHIM_CMD is an intentional word-split
  # seam (same convention as run_agent_battery.sh's GOOSE_RUN_ARGS).
  $CHAT_SHIM_CMD --bin "$BIN" --vindex "$VINDEX" --port "$PORT" "$@" \
    > "$log" 2>&1 &
  SHIM_PID=$!

  health_gate "$PORT" "$SHIM_PID" "$log"

  OUT_DIR="$OUT" bash "$BATTERY_SCRIPT" \
    --questions "$QUESTIONS" --label "$label" --port "$PORT"
  local battery_rc=$?

  kill "$SHIM_PID" 2>/dev/null || true
  wait "$SHIM_PID" 2>/dev/null || true
  SHIM_PID=""

  return "$battery_rc"
}

json_escape() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

run_pass patched --patches-dir "$PATCHES_DIR" || {
  echo "error: patched pass failed" >&2
  exit 1
}
run_pass ablation --no-patches || {
  echo "error: ablation pass failed" >&2
  exit 1
}

# -- summary: per-question attribution control (N4) --
results_path="$OUT/goose-demo-results.jsonl"
summary_path="$OUT/goose-demo-summary.md"
: > "$results_path"

mapfile -t Q_LINES < "$QUESTIONS"
mapfile -t EXPECTED_LINES < "$EXPECTED"

n=0
pass_count=0
declare -a TABLE_ROWS=()

for qline in "${Q_LINES[@]}"; do
  [ -z "$qline" ] && continue
  n=$((n + 1))
  expected="${EXPECTED_LINES[$((n - 1))]:-}"
  patched_file="$OUT/transcripts/patched-q${n}.txt"
  ablation_file="$OUT/transcripts/ablation-q${n}.txt"

  patched_hit=false
  ablation_hit=false
  [ -f "$patched_file" ] && grep -qF -- "$expected" "$patched_file" && patched_hit=true
  [ -f "$ablation_file" ] && grep -qF -- "$expected" "$ablation_file" && ablation_hit=true

  if $patched_hit && ! $ablation_hit; then
    attribution="agent-vindex"; verdict="PASS"; pass_count=$((pass_count + 1))
  elif $patched_hit && $ablation_hit; then
    attribution="confounded"; verdict="FAIL"
  else
    attribution="absent"; verdict="FAIL"
  fi

  TABLE_ROWS+=("$n|$expected|$patched_hit|$ablation_hit|$verdict ($attribution)")

  printf '{"question":%d,"expected":%s,"patched_hit":%s,"ablation_hit":%s,"attribution":%s,"verdict":%s}\n' \
    "$n" "$(json_escape "$expected")" "$patched_hit" "$ablation_hit" \
    "$(json_escape "$attribution")" "$(json_escape "$verdict")" \
    >> "$results_path"
done

{
  echo "# Goose Demo Summary"
  echo ""
  echo "**${pass_count}/${n} agent-vindex-attributed**"
  echo ""
  echo "| question | expected | patched hit | ablation hit | verdict |"
  echo "|----------|----------|-------------|--------------|---------|"
  for row in "${TABLE_ROWS[@]+"${TABLE_ROWS[@]}"}"; do
    IFS='|' read -r r_n r_exp r_p r_a r_verdict <<< "$row"
    echo "| ${r_n} | ${r_exp} | ${r_p} | ${r_a} | ${r_verdict} |"
  done
} > "$summary_path"

echo "${pass_count}/${n} agent-vindex-attributed -> $summary_path"
exit 0
