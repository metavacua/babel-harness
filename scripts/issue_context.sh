#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# issue_context.sh <issue-number> — AUTONOMOUS target discovery for a babel
# issue-fix, meant to run IN a CI runner (needs gh auth + the repo checkout; no
# model). Fetches the issue, deterministically extracts the file references from
# its body (path-shaped tokens that ACTUALLY exist in the checkout — handles
# extensionless paths like bin/coding-agent, strips :line suffixes), reads those
# files, and prints a context-loaded task for babel. The harness does discovery;
# the model only does the fix within the provided context.
#
# Env: GH_REPO (default metavacua/babel-harness). Prints the task to stdout;
# prints the discovered file list to stderr.
set -uo pipefail
N="${1:?usage: issue_context.sh <issue-number>}"
REPO="${GH_REPO:-metavacua/babel-harness}"

body="$(gh issue view "$N" --repo "$REPO" --json title,body --jq '.title + "\n\n" + (.body // "")' 2>/dev/null)"
[ -n "$body" ] || { echo "issue_context: could not fetch issue #$N from $REPO" >&2; exit 1; }

# Path-shaped tokens (must contain a '/'), strip a trailing :line or :line-line,
# dedup, keep only those that exist as files in the checkout.
files="$(printf '%s' "$body" \
  | grep -aoE '[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+' \
  | sed -E 's/[:.,)]+$//' \
  | sort -u \
  | while read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done)"

echo "issue_context: #$N discovered target files:" >&2
printf '%s\n' "${files:-(none)}" >&2

printf 'Work this babel-harness issue #%s. Edit ONLY the files shown below; keep the test suite green; make the smallest change that resolves it.\n\n=== ISSUE ===\n%s\n' "$N" "$body"
if [ -n "$files" ]; then
  printf '\n=== TARGET FILES (edit only these) ===\n'
  for f in $files; do
    printf '\n--- %s ---\n```\n%s\n```\n' "$f" "$(cat "$f")"
  done
fi
