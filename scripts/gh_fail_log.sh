#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Shared primitive: print the bounded log tail of the failed job(s) in a run.
# The one place the failing-job log-fetch lives — used by ci_diagnose.sh and the
# fix step, so the job-selection filter and tail bounds are maintained once.
#
# Env: GITHUB_REPOSITORY, RUN_ID. Prints up to 120 lines (60 per failed job).
set -uo pipefail
gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}/jobs" \
  --jq '.jobs[] | select(.conclusion=="failure") | .id' 2>/dev/null | while read -r jid; do
    gh api "repos/${GITHUB_REPOSITORY}/actions/jobs/${jid}/logs" 2>/dev/null | tail -60
  done | tail -120
