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

echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
