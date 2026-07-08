#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }; bad(){ echo "  FAIL: $1: $2"; ((FAIL++))||true; }

# A cell whose command emits the expected marker -> exit 0
CELL=selftest-ok LARQL_FAKE_OUT="the answer is OK" bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && ok "cell with expected marker exits 0" || bad "expected 0" "$?"
# A cell whose command emits nothing/wrong -> exit non-zero
CELL=selftest-ok LARQL_FAKE_OUT="nope" bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && bad "cell without marker should fail" "got 0" || ok "cell missing marker exits non-zero"
# Non-zero exit must FAIL even if output contains an "ok" substring
CELL=selftest-ok LARQL_FAKE_OUT="loading tokenizer" LARQL_FAKE_RC=1 bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && bad "non-zero rc with substring should fail" "got 0" || ok "non-zero rc exits non-zero despite substring"
# Substring-only output (no standalone OK) must FAIL under the word boundary
CELL=selftest-ok LARQL_FAKE_OUT="tokenizer" bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && bad "substring-only match should fail" "got 0" || ok "substring-only output exits non-zero (word boundary)"

# --- hf cells: fetch-then-run-LOCAL-DIR (root cause 2026-07-08) ------------
# larql's hf:// resolve is metadata-only (weights deferred to a function with
# no callers), so `larql run hf://...` dies on a fresh cache. The cells must
# therefore run larql against a COMPLETE local snapshot dir. LARQL_CMD_LOG
# records the argv the cell would exec; the fake seam keeps this network-free.
LOG="$(mktemp)"
CELL=run-hf-granite-q4k LARQL_FAKE_OUT="OK" LARQL_CMD_LOG="$LOG" bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && ok "granite cell wired (fake seam, marker)" || bad "granite cell should pass under fake marker" "$?"
grep -q 'larql run /tmp/granite.vindex' "$LOG" \
  && ok "granite cell runs a LOCAL dir, not hf://" || bad "granite cell must run local dir" "$(cat "$LOG")"
grep -q 'hf://' "$LOG" \
  && bad "granite run argv must not regress to hf://" "$(cat "$LOG")" || ok "no hf:// in granite run argv"
# the fetch half of the fix must be observable too: correct repo + dest, and
# the fetch must precede the run (deleting fetch_vindex, or swapping its args,
# must turn this suite red — mutation gap found 2026-07-08).
grep -qx 'fetch_vindex chrishayuk/granite-4.1-3b-q4k-vindex /tmp/granite.vindex' "$LOG" \
  && ok "granite cell fetches the COMPLETE snapshot (repo -> dest)" || bad "granite fetch argv" "$(cat "$LOG")"
[ "$(grep -n 'fetch_vindex' "$LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n 'larql run' "$LOG" | head -1 | cut -d: -f1)" ] 2>/dev/null \
  && ok "granite fetch happens BEFORE the run" || bad "granite fetch/run order" "$(cat "$LOG")"
rm -f "$LOG"

LOG="$(mktemp)"
CELL=run-hf-gemma LARQL_FAKE_OUT="OK" LARQL_CMD_LOG="$LOG" bash "$ROOT/scripts/larql_cell.sh" >/dev/null 2>&1 \
  && ok "gemma cell wired (fake seam, marker)" || bad "gemma cell should pass under fake marker" "$?"
grep -q 'larql run /tmp/gemma.vindex' "$LOG" \
  && ok "gemma cell runs a LOCAL dir, not hf://" || bad "gemma cell must run local dir" "$(cat "$LOG")"
rm -f "$LOG"

echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
