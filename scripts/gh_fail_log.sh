#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Shared primitive: print the bounded log tail of the failed job(s) in a run.
# The one place the failing-job log-fetch lives. Intended consumer: a real
# babel-based CI diagnosis (feed this log to `bin/babel` as the task), not a
# bespoke model call.
#
# Env: GITHUB_REPOSITORY, RUN_ID. Prints up to 120 lines (60 per failed job).
set -uo pipefail
gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}/jobs" \
  --jq '.jobs[] | select(.conclusion=="failure") | .id' 2>/dev/null | while read -r jid; do
    gh api "repos/${GITHUB_REPOSITORY}/actions/jobs/${jid}/logs" 2>/dev/null | tail -60
  done | tail -120
