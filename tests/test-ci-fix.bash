#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }; bad(){ echo "  FAIL: $1"; ((FAIL++))||true; }
tmp="$(mktemp -d)"; cd "$tmp"; git init -q; git config user.email t@t; git config user.name t
printf 'exit 1\n' > cmd.sh; git add cmd.sh; git commit -qm init      # FAIL_CMD fails until patched
DIFF="$(printf -- '--- a/cmd.sh\n+++ b/cmd.sh\n@@ -1 +1 @@\n-exit 1\n+exit 0\n')"
out="$(FIX_FAKE_DIFF="$DIFF" FAIL_CMD='bash cmd.sh' TARGET_FILE='cmd.sh' FAIL_LOG='cmd failed' \
       bash "$ROOT/scripts/ci_fix.sh" 2>/dev/null)"; rc=$?
echo "$out" | grep -qi 'verified: \*\*FIXED\*\*' && ok "records FIXED when re-run passes" || bad "no FIXED"
[ "$rc" -eq 0 ] && ok "exits 0 when fix verified" || bad "expected exit 0"
[ -f artifact/proposed-fix.patch ] && ok "records the patch artifact" || bad "no artifact"
echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
