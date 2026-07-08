#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# merge_ladder.sh — deterministic merge-progress ladder for an autonomous
# babel-issue run. Extracted from babel-issue.yml (same motive as
# larql_cell.sh: the inline block had no rejection power — the 2026-07-08 #19
# run wrote an unrelated untracked src/ blob and "PASSed" every rung).
#
# Scores the WORKING TREE of the current git repo. The ladder is a REPORT
# (every rung always printed, stdout = markdown); the exit code is the PR
# DECISION, gated on the target oracle: babel earns a PR only by touching a
# file the issue actually declared.
#
# Env:
#   TARGETS_FILE  file of declared target paths, one per line (from
#                 issue_context.sh TARGETS_OUT). Missing/empty => oracle FAIL.
#   LADDER_TESTS  space-separated rung-3 suites (default: the hermetic mock
#                 certificate suites). Empty string skips rung 3.
# Exit: 0 = a declared target moved (PR-worthy)
#       10 = no change at all          (honest halt)
#       11 = changes, but no target hit (honest halt — the GIGO guard)
# Graded by tests/test-merge-ladder.bash.
set -uo pipefail

TARGETS_FILE="${TARGETS_FILE:-}"
LADDER_TESTS="${LADDER_TESTS-tests/test-babel.bash tests/test-coding-agent.bash tests/test-pi-harness.bash tests/test-larql-cell.bash tests/test-vindex-completeness.bash tests/test-component-check.bash tests/test-merge-ladder.bash tests/test-issue-context.bash}"

# --- changed FILES (not dirs): tracked modifications + files inside untracked
# dirs. `git status --porcelain` prints `?? src/` for a new dir, which is how
# the #19 blob dodged bash -n; ls-files --others resolves to the files within.
mapfile -t CHANGED < <({ git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard; } | sort -u | sed '/^$/d')

# rung 0 — babel touched anything at all
if [ "${#CHANGED[@]}" -eq 0 ]; then
  echo "- rung 0 (touched a file): **FAIL** — babel produced no change"
  exit 10
fi
echo "- rung 0 (touched a file): **PASS** — ${#CHANGED[@]} file(s)"
for f in "${CHANGED[@]}"; do echo "  - \`$f\`"; done

# rung 1 — THE ORACLE: did a DECLARED TARGET move?
HITS=()
if [ -n "$TARGETS_FILE" ] && [ -f "$TARGETS_FILE" ]; then
  for f in "${CHANGED[@]}"; do
    grep -qxF "$f" "$TARGETS_FILE" && HITS+=("$f")
  done
fi
if [ "${#HITS[@]}" -gt 0 ]; then
  echo "- rung 1 (touched a DECLARED TARGET): **PASS** — ${HITS[*]}"
else
  echo "- rung 1 (touched a DECLARED TARGET): **FAIL** — changed files match no declared target"
fi

# rung 2 — changed shell files (incl. inside untracked dirs) still parse.
# Honest accounting: a PASS that linted zero files is a vacuous certificate,
# so when nothing was lintable the rung says SKIP, and a PASS carries a count.
# Shell files are detected by path pattern OR shebang: run 28970562690 truncated
# tests/mocks/goose (shebang bash, no extension) and the pattern alone SKIPped it.
_is_shell() {
  case "$1" in bin/*|*.sh|*.bash) return 0 ;; esac
  head -c 64 "$1" 2>/dev/null | head -1 | grep -qE '^#!.*\b(ba)?sh\b'
}
rc2=0; n2=0
for f in "${CHANGED[@]}"; do
  [ -f "$f" ] && _is_shell "$f" && { n2=$((n2+1)); bash -n "$f" 2>/dev/null || { echo "  - bash -n FAIL: \`$f\`"; rc2=1; }; }
done
if [ "$n2" -eq 0 ]; then
  echo "- rung 2 (bash -n clean): **SKIP** — no shell files among the changes"
elif [ $rc2 = 0 ]; then
  echo "- rung 2 (bash -n clean): **PASS** — $n2 file(s) checked"
else
  echo "- rung 2 (bash -n clean): **FAIL**"
fi

# rung 3 — the hermetic certificate suites (report-only; operator gates)
if [ -n "$LADDER_TESTS" ]; then
  rc3=0
  for t in $LADDER_TESTS; do
    if bash "$t" >/dev/null 2>&1; then echo "  - $t: PASS"; else echo "  - $t: FAIL"; rc3=1; fi
  done
  [ $rc3 = 0 ] && echo "- rung 3 (certificate suites green): **PASS**" || echo "- rung 3 (certificate suites green): **FAIL**"
fi

[ "${#HITS[@]}" -gt 0 ] && exit 0 || exit 11
