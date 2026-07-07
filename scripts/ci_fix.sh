#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Ephemeral-VM debug-and-fix: ask the known-working babel-local model for a
# unified diff that fixes FAIL_CMD, apply it to THIS runner's checkout, re-run
# FAIL_CMD, and RECORD the patch + whether it now passes. Nothing is pushed; the
# disposable runner is the sandbox, the recorded artifact is the only survivor.
# Env: FAIL_CMD, TARGET_FILE, FAIL_LOG (required); OLLAMA_URL, DIAG_MODEL.
# Seam: FIX_FAKE_DIFF bypasses the model.
set -uo pipefail
: "${FAIL_CMD:?}"; : "${TARGET_FILE:?}"; : "${FAIL_LOG:?}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"; DIAG_MODEL="${DIAG_MODEL:-qwen2.5:1.5b}"

src="$(cat "$TARGET_FILE" 2>/dev/null || true)"
if [ -n "${FIX_FAKE_DIFF:-}" ]; then
  DIFF="$FIX_FAKE_DIFF"
else
  PROMPT="A CI command failed. Output ONLY a unified diff (git apply format) against $TARGET_FILE that fixes it — no prose. LOG:
$FAIL_LOG

--- $TARGET_FILE ---
$src"
  DIFF="$(python3 "$HERE/ollama_generate.py" "$OLLAMA_URL" "$DIAG_MODEL" "$PROMPT" 180)"
fi

verdict=NOT-FIXED
printf '%s\n' "$DIFF" > /tmp/fix.patch
if git apply /tmp/fix.patch 2>/dev/null; then
  bash -c "$FAIL_CMD" >/tmp/rerun.log 2>&1 && verdict=FIXED
  git apply -R /tmp/fix.patch 2>/dev/null || true   # leave the tree clean; the patch is the artifact
fi

mkdir -p artifact; printf '%s\n' "$DIFF" > artifact/proposed-fix.patch
REPORT="## babel-local debug-and-fix (qwen2.5:1.5b, ephemeral)
- verified: **$verdict** (re-ran \`$FAIL_CMD\`)
\`\`\`diff
$DIFF
\`\`\`
<sub>Produced and verified in the disposable runner; nothing pushed. Apply \`artifact/proposed-fix.patch\` manually if the fix is good.</sub>"
printf '%s\n' "$REPORT"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '%s\n' "$REPORT" >> "$GITHUB_STEP_SUMMARY"
[ "$verdict" = "FIXED" ]
