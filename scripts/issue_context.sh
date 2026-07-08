#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# issue_context.sh <issue-number> — AUTONOMOUS, BOUNDED target discovery for a
# babel issue-fix, run IN a CI runner (gh + checkout; no model). Fetches the
# issue, extracts path[:line] references that exist in the checkout, and emits a
# SMALL, TARGETED context: the CITED line-region (± a few lines) of each source
# file, excluding tangential docs, hard-capped in size.
#
# Why bounded: measured (babel-context-probe) that qwen2.5:0.5b stops emitting
# tool calls above ~12-24K chars of context. Over-loading whole files + audit
# docs pushed it past that cliff and it produced plain text instead of editing.
# So the harness must keep the context under the model's tool-calling budget.
#
# Env: GH_REPO (default metavacua/babel-harness), CTX_CAP (default 8000 chars),
#      REGION_PAD (default 8 lines). Task -> stdout; discovery -> stderr.
#      TARGETS_OUT (optional): also write the DECLARED TARGET files (cited paths
#      that exist in the checkout, one per line) to this file — the
#      machine-readable oracle input merge_ladder.sh rung 1 consumes.
set -uo pipefail
N="${1:?usage: issue_context.sh <issue-number>}"
REPO="${GH_REPO:-metavacua/babel-harness}"
CAP="${CTX_CAP:-8000}"
PAD="${REGION_PAD:-8}"

body="$(gh issue view "$N" --repo "$REPO" --json title,body --jq '.title + "\n\n" + (.body // "")' 2>/dev/null)"
[ -n "$body" ] || { echo "issue_context: could not fetch issue #$N from $REPO" >&2; exit 1; }

# path[:line[-line]] tokens that contain a '/', excluding docs/ (tangential inventory).
refs="$(printf '%s' "$body" \
  | grep -aoE '[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+(:[0-9]+(-[0-9]+)?)?' \
  | sed -E 's/[.,)]+$//' \
  | grep -vE '^docs/' \
  | sort -u)"

echo "issue_context: #$N discovered refs:" >&2; printf '%s\n' "${refs:-(none)}" >&2

# machine-readable target list for the merge ladder's oracle rung. Paths are
# normalized to git's root-relative form (no './' — git never prints it, and
# the oracle's grep -qxF is an exact-line match). Known limitation: targets are
# files that already EXIST in the checkout, so file-CREATION issues declare no
# target and can never earn a PR — matching the region-extraction design above.
if [ -n "${TARGETS_OUT:-}" ]; then
  for ref in $refs; do f="${ref%%:*}"; f="${f#./}"; [ -f "$f" ] && printf '%s\n' "$f"; done | sort -u > "$TARGETS_OUT"
  echo "issue_context: targets -> $TARGETS_OUT ($(wc -l < "$TARGETS_OUT") file(s))" >&2
fi

# emit each cited region (bounded), tracking a total char budget.
emit_regions() {
  local used=0
  for ref in $refs; do
    local f="${ref%%:*}" lines="" start end
    [ -f "$f" ] || continue
    case "$ref" in
      *:*) lines="${ref#*:}"; start="${lines%%-*}"; end="${lines#*-}"; [ "$end" = "$lines" ] && end="$start" ;;
      *)   start=1; end=$(wc -l < "$f") ;;
    esac
    start=$(( start > PAD ? start - PAD : 1 )); end=$(( end + PAD ))
    local region; region="$(sed -n "${start},${end}p" "$f")"
    local chunk; chunk="$(printf -- '--- %s (lines %s-%s) ---\n```\n%s\n```\n' "$f" "$start" "$end" "$region")"
    local len=${#chunk}
    if [ $(( used + len )) -gt "$CAP" ]; then
      echo "issue_context: cap ${CAP} reached; omitting $f (and any later refs)" >&2; break
    fi
    printf '%s\n' "$chunk"; used=$(( used + len ))
  done
}

printf 'Work babel-harness issue #%s. Use the write/edit tool to change ONLY the file(s) shown below; keep the test suite green; make the smallest change that resolves it.\n\n=== ISSUE ===\n%s\n\n=== TARGET (edit here) ===\n' "$N" "$body"
emit_regions
