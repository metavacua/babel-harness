#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Tests for scripts/component_check.py — scores whether a babel-issue run's DOING
# components FIRED (function, not quality): explored (read/search tool) + acted
# (write/edit tool). Exit 0 iff both fired. Quality of output is NOT judged.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHK="$ROOT/scripts/component_check.py"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }
bad(){ echo "  FAIL: $1"; ((FAIL++))||true; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# 1. explored (read tool) + acted (write tool) -> functioned (exit 0)
printf '%s\n' '{"type":"toolCall","id":"c1","name":"read","arguments":{"path":"bin/coding-agent"}}' \
              '{"type":"toolCall","id":"c2","name":"write","arguments":{"path":"bin/coding-agent"}}' > "$tmp/full"
if python3 "$CHK" "$tmp/full" >/tmp/cc.out 2>&1; then
  grep -q 'explored: YES' /tmp/cc.out && grep -q 'acted: YES' /tmp/cc.out \
    && ok "read+write transcript scores explored+acted, exit 0" || bad "reported wrong for full transcript"
else bad "full transcript should exit 0"; fi

# 2. no tool calls (pure confabulation) -> did NOT function (exit != 0), explored NO
printf '%s\n' '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"lets break it down step by step"}]}}' > "$tmp/none"
python3 "$CHK" "$tmp/none" >/tmp/cc.out 2>&1 && bad "no-tool transcript should exit non-zero" || {
  grep -q 'explored: NO' /tmp/cc.out && ok "confabulation (no tools) -> explored NO, exit non-zero" || bad "should report explored NO"; }

# 3. bash-grep (explore) + edit (act) -> functioned (exit 0)
printf '%s\n' '{"type":"toolCall","id":"c1","name":"bash","arguments":{"command":"grep -n _run_goose_call bin/coding-agent"}}' \
              '{"type":"toolCall","id":"c2","name":"edit","arguments":{"path":"bin/coding-agent"}}' > "$tmp/bash"
python3 "$CHK" "$tmp/bash" >/tmp/cc.out 2>&1 && grep -q 'explored: YES' /tmp/cc.out \
  && ok "bash-grep counts as explore; edit counts as act; exit 0" || bad "bash-grep/edit misclassified"

# 4. write only, never explored -> did NOT function (exit != 0)
printf '%s\n' '{"type":"toolCall","id":"c1","name":"write","arguments":{"path":"src/bin/coding-agent"}}' > "$tmp/blind"
python3 "$CHK" "$tmp/blind" >/tmp/cc.out 2>&1 && bad "acted-without-exploring should exit non-zero" || {
  grep -q 'explored: NO' /tmp/cc.out && grep -q 'acted: YES' /tmp/cc.out \
    && ok "wrote-without-exploring -> explored NO, acted YES, exit non-zero" || bad "should be explored NO acted YES"; }

echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
