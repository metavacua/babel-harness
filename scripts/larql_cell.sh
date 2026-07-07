#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# One larql-matrix cell with a REAL verdict: run the cell's larql invocation,
# require its output to contain the expected marker, else exit non-zero. This is
# the rejection power the inline `set +e` case block lacked (green-but-broken).
#
# Env: CELL (required), LARQL_BIN_DIR (dir holding the larql binary; prepended
#      to PATH). Test seam: LARQL_FAKE_OUT bypasses larql and is treated as the
#      command output (for tests/test-larql-cell.bash).
set -uo pipefail
CELL="${CELL:?CELL required}"
[ -n "${LARQL_BIN_DIR:-}" ] && export PATH="$LARQL_BIN_DIR:$PATH"
Q="Reply with exactly one word: OK"

run() {  # echoes the command's combined output; returns its exit code
  if [ -n "${LARQL_FAKE_OUT:-}" ]; then printf '%s' "$LARQL_FAKE_OUT"; return 0; fi
  "$@" 2>&1
}

case "$CELL" in
  selftest-ok)          out="$(run true)";               marker='OK' ;;
  run-hf-granite-q4k)   out="$(run larql run hf://chrishayuk/granite-4.1-3b-q4k-vindex "$Q" -n 8)"; marker='OK' ;;
  run-hf-gemma-f16)     out="$(run larql run hf://chrishayuk/gemma-3-4b-it-vindex "$Q" -n 8)";     marker='OK' ;;
  *) echo "larql_cell: unknown cell: $CELL" >&2; exit 2 ;;
esac
rc=$?
echo "=== cell $CELL output ==="; printf '%s\n' "$out"
if [ "$rc" -ne 0 ] || ! printf '%s' "$out" | grep -qiE "$marker"; then
  echo "=== cell $CELL VERDICT: FAIL (rc=$rc, marker '/$marker/' not found) ==="
  exit 1
fi
echo "=== cell $CELL VERDICT: PASS ==="
