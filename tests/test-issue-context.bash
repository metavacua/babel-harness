#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Acceptance spec for scripts/issue_context.sh's TARGETS_OUT contract: the
# machine-readable list of declared target files that merge_ladder.sh's oracle
# rung consumes. Hermetic — `gh` is stubbed on PATH; file-existence checks run
# against the real checkout.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; SCRIPT="$ROOT/scripts/issue_context.sh"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }; bad(){ echo "  FAIL: $1: $2"; ((FAIL++))||true; }

T="$(mktemp -d)"
cat > "$T/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$MOCK_ISSUE_BODY"
EOF
chmod +x "$T/gh"

# Body cites: a real file with a line range, a docs/ file (excluded by design),
# and a nonexistent path (must not become a target).
BODY=$'stub issue title\n\nBug in bin/babel:46-52 — see docs/adr/0001-babel-unified-dispatcher.md and no/such/file.sh'

echo "== test-issue-context (TARGETS_OUT: the oracle's input) =="

cd "$ROOT"
OUT="$(MOCK_ISSUE_BODY="$BODY" PATH="$T:$PATH" TARGETS_OUT="$T/targets.txt" bash "$SCRIPT" 7 2>"$T/err.log")"; RC=$?

[ "$RC" = "0" ] && ok "exits 0 with TARGETS_OUT set" || bad "exit code" "rc=$RC $(cat "$T/err.log")"
[ -f "$T/targets.txt" ] && ok "writes the targets file" || bad "targets file" "not written"
grep -qx 'bin/babel' "$T/targets.txt" && ok "existing cited file becomes a target" || bad "bin/babel target" "$(cat "$T/targets.txt" 2>/dev/null)"
grep -q 'docs/' "$T/targets.txt" && bad "docs/ excluded from targets" "$(cat "$T/targets.txt")" || ok "docs/ refs stay excluded"
grep -q 'no/such/file.sh' "$T/targets.txt" && bad "nonexistent refs excluded" "$(cat "$T/targets.txt")" || ok "nonexistent refs excluded"
echo "$OUT" | grep -q -- '--- bin/babel' && ok "task still embeds the cited region" || bad "task region" "$(echo "$OUT" | head -5)"

# './'-prefixed citations must be normalized: merge_ladder compares against
# git's root-relative paths, which never carry './' (review finding 2026-07-08).
BODY3=$'title\n\nsee ./bin/babel:46-52 for the bug'
MOCK_ISSUE_BODY="$BODY3" PATH="$T:$PATH" TARGETS_OUT="$T/targets3.txt" bash "$SCRIPT" 7 >/dev/null 2>&1
grep -qx 'bin/babel' "$T/targets3.txt" && ok "./-cited path normalized to git-relative" || bad "./ normalization" "$(cat "$T/targets3.txt" 2>/dev/null)"
grep -q '^\./' "$T/targets3.txt" && bad "no ./ prefix survives in targets" "$(cat "$T/targets3.txt")" || ok "no ./ prefix in targets"

# Backward compat: without TARGETS_OUT the script behaves as before.
OUT2="$(MOCK_ISSUE_BODY="$BODY" PATH="$T:$PATH" bash "$SCRIPT" 7 2>/dev/null)"; RC2=$?
[ "$RC2" = "0" ] && ok "no TARGETS_OUT: still exits 0" || bad "no TARGETS_OUT exit" "rc=$RC2"
echo "$OUT2" | grep -q 'Work babel-harness issue #7' && ok "no TARGETS_OUT: task unchanged" || bad "task text" "$(echo "$OUT2" | head -3)"

echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
