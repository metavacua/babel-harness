#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# One larql-matrix cell with a REAL verdict: run the cell's larql invocation,
# require its output to contain the expected marker, else exit non-zero. This is
# the rejection power the inline `set +e` case block lacked (green-but-broken).
#
# ROOT CAUSE (2026-07-08, traced in larql source): `larql run hf://owner/name`
# resolves via the METADATA-ONLY path — resolve_model -> resolve_hf_vindex
# downloads index.json + small metadata and defers every weight tensor to
# download_hf_weights(), which has NO production callers (larql pull's eager
# path fetches only core files: metadata + embeddings/gate_vectors). So on a
# fresh cache the engine opens a weight file that was never downloaded and dies
# with the bare "IO error: No such file or directory (os error 2)". No larql
# invocation sequence can currently fix that; the cell therefore fetches the
# COMPLETE published snapshot itself and hands larql a LOCAL directory — the
# same local-dir pattern larql-vindex.yml's green serve path uses. This also
# answers the header's old local-path question: `larql run <dir>` resolves
# directly (resolve_model step 2, is_dir), no hf:// prepend.
#
# Env: CELL (required), LARQL_BIN_DIR (dir holding the larql binary; prepended
#      to PATH). Test seams (tests/test-larql-cell.bash): LARQL_FAKE_OUT
#      bypasses larql/network and is treated as the command output;
#      LARQL_CMD_LOG records each run() argv.
set -uo pipefail
CELL="${CELL:?CELL required}"
[ -n "${LARQL_BIN_DIR:-}" ] && export PATH="$LARQL_BIN_DIR:$PATH"
Q="Reply with exactly one word: OK"
marker='\bOK\b'   # success marker for every cell; override inside an arm only if one differs

run() {  # echoes the command's combined output; returns its exit code
  [ -n "${LARQL_CMD_LOG:-}" ] && printf '%s\n' "$*" >> "$LARQL_CMD_LOG"
  if [ -n "${LARQL_FAKE_OUT:-}" ]; then printf '%s' "$LARQL_FAKE_OUT"; return "${LARQL_FAKE_RC:-0}"; fi
  "$@" 2>&1
}

# fetch_vindex REPO DEST — download the COMPLETE published vindex snapshot
# (weights included) to a local dir; see root-cause note above. Needs
# huggingface_hub (provisioned by the workflow). Skipped under the fake seam,
# but LOGGED first so the tests can assert the fetch half of the fix exists
# (mutation gap 2026-07-08: deleting the fetch was invisible to the suite).
fetch_vindex() {
  [ -n "${LARQL_CMD_LOG:-}" ] && printf 'fetch_vindex %s %s\n' "$1" "$2" >> "$LARQL_CMD_LOG"
  [ -n "${LARQL_FAKE_OUT:-}" ] && return 0
  python3 - "$1" "$2" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])
print(f"fetched {sys.argv[1]} -> {sys.argv[2]}")
PY
}

case "$CELL" in
  selftest-ok)          out="$(run true)";               rc=$? ;;
  run-hf-granite-q4k)   # direct run on the published q4k granite vindex (serve hit Io NotFound)
    fetch_vindex chrishayuk/granite-4.1-3b-q4k-vindex /tmp/granite.vindex || echo "larql_cell: fetch failed — run will fail honestly" >&2
    out="$(run larql run /tmp/granite.vindex "$Q" -n 8)"; rc=$? ;;
  run-hf-gemma)         # direct run on the published gemma vindex (serve 500'd; repo files are legacy-q4k-named)
    fetch_vindex chrishayuk/gemma-3-4b-it-vindex /tmp/gemma.vindex || echo "larql_cell: fetch failed — run will fail honestly" >&2
    out="$(run larql run /tmp/gemma.vindex "$Q" -n 8)"; rc=$? ;;
  *) echo "larql_cell: unknown cell: $CELL" >&2; exit 2 ;;
esac
echo "=== cell $CELL output ==="; printf '%s\n' "$out"
if [ "$rc" -ne 0 ] || ! printf '%s' "$out" | grep -qiE "$marker"; then
  echo "=== cell $CELL VERDICT: FAIL (rc=$rc, marker '/$marker/' not found) ==="
  exit 1
fi
echo "=== cell $CELL VERDICT: PASS ==="
