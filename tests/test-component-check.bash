#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Tests for scripts/component_check.py — scores whether a babel-issue run's three
# DOING components FIRED (function, not quality), each detected from babel's own
# tool calls:
#   read_issue: babel read the issue ITSELF (a bash `gh issue view` call)
#   explored:   babel read/searched the repo (read tool, or bash grep/cat/ls/find/head/sed)
#   acted:      babel wrote/edited a file (write/edit tool)
# Exit 0 iff all three fired. Output quality is NOT judged.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHK="$ROOT/scripts/component_check.py"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }
bad(){ echo "  FAIL: $1"; ((FAIL++))||true; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

gh_call='{"type":"toolCall","id":"c0","name":"bash","arguments":{"command":"gh issue view 19"}}'
read_call='{"type":"toolCall","id":"c1","name":"read","arguments":{"path":"bin/coding-agent"}}'
grep_call='{"type":"toolCall","id":"c1","name":"bash","arguments":{"command":"grep -n _run_goose_call bin/coding-agent"}}'
write_call='{"type":"toolCall","id":"c2","name":"write","arguments":{"path":"bin/coding-agent"}}'
edit_call='{"type":"toolCall","id":"c2","name":"edit","arguments":{"path":"bin/coding-agent"}}'

# 1. ALL THREE fired (gh-issue + read + write) -> functioned, exit 0
printf '%s\n' "$gh_call" "$read_call" "$write_call" > "$tmp/full"
if python3 "$CHK" "$tmp/full" >/tmp/cc.out 2>&1; then
  grep -q 'read_issue: YES' /tmp/cc.out && grep -q 'explored: YES' /tmp/cc.out && grep -q 'acted: YES' /tmp/cc.out \
    && ok "gh-issue + read + write -> all three fired, exit 0" || bad "full transcript misreported"
else bad "full three-component transcript should exit 0"; fi

# 2. no tools at all -> nothing fired, exit non-zero
printf '%s\n' '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"lets break it down"}]}}' > "$tmp/none"
python3 "$CHK" "$tmp/none" >/tmp/cc.out 2>&1 && bad "no-tool transcript should exit non-zero" || {
  grep -q 'read_issue: NO' /tmp/cc.out && grep -q 'explored: NO' /tmp/cc.out && grep -q 'acted: NO' /tmp/cc.out \
    && ok "confabulation -> all three NO, exit non-zero" || bad "should report all NO"; }

# 3. MISSING read_issue (explored+acted but never read the issue itself) -> exit non-zero
printf '%s\n' "$grep_call" "$edit_call" > "$tmp/noissue"
python3 "$CHK" "$tmp/noissue" >/tmp/cc.out 2>&1 && bad "missing read_issue should exit non-zero" || {
  grep -q 'read_issue: NO' /tmp/cc.out && grep -q 'explored: YES' /tmp/cc.out && grep -q 'acted: YES' /tmp/cc.out \
    && ok "no gh-issue -> read_issue NO (explored+acted YES), exit non-zero" || bad "should be read_issue NO only"; }

# 4. MISSING explored (gh-issue + write, but never read/searched the repo) -> exit non-zero
printf '%s\n' "$gh_call" "$write_call" > "$tmp/noexplore"
python3 "$CHK" "$tmp/noexplore" >/tmp/cc.out 2>&1 && bad "missing explored should exit non-zero" || {
  grep -q 'explored: NO' /tmp/cc.out && grep -q 'read_issue: YES' /tmp/cc.out && grep -q 'acted: YES' /tmp/cc.out \
    && ok "no repo read -> explored NO (read_issue+acted YES), exit non-zero" || bad "should be explored NO only"; }

# 5. MISSING acted (gh-issue + read, but never wrote) -> exit non-zero
printf '%s\n' "$gh_call" "$read_call" > "$tmp/noact"
python3 "$CHK" "$tmp/noact" >/tmp/cc.out 2>&1 && bad "missing acted should exit non-zero" || {
  grep -q 'acted: NO' /tmp/cc.out && grep -q 'read_issue: YES' /tmp/cc.out && grep -q 'explored: YES' /tmp/cc.out \
    && ok "no write -> acted NO (read_issue+explored YES), exit non-zero" || bad "should be acted NO only"; }

echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
