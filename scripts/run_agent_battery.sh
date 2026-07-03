#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Drive goose against a live (contained, already-started) larql-server and record
# transcripts. The server is started by the orchestrator (run_matrix/LarqlServer),
# NOT here — one server max (C4). Liveness is a hard gate (N7).
#
# Environment seams:
#   LARQL_PORT — larql server port (default: 8282)
#   GOOSE_BIN — path to goose binary (default: goose)
#   GOOSE_RUN_ARGS — goose run arguments (default: "run --no-session --quiet --text")
#   GOOSE_MODEL — model id the server advertises (default: legal-theory-vindex)
#   OUT_DIR — output directory for transcripts (default: $HOME/work/artifacts/agent)
#
# CRITICAL wiring: goose only routes to the local server when GOOSE_PROVIDER=openai
# AND GOOSE_MODEL name the openai-compat backend. Setting OPENAI_BASE_URL alone is
# NOT sufficient — goose falls back to its configured default provider (e.g.
# openrouter/anthropic) and the demo silently measures a remote cloud model instead
# of the vindex. Both vars are exported per-invocation below.
set -uo pipefail

LARQL_PORT="${LARQL_PORT:-8282}"
GOOSE_BIN="${GOOSE_BIN:-goose}"
GOOSE_RUN_ARGS="${GOOSE_RUN_ARGS:-run --no-session --quiet --text}"
GOOSE_MODEL="${GOOSE_MODEL:-legal-theory-vindex}"
OUT_DIR="${OUT_DIR:-$HOME/work/artifacts/agent}"
QUESTIONS=""
LABEL="patched"

while [ $# -gt 0 ]; do
  case "$1" in
    --questions) QUESTIONS="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --port) LARQL_PORT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Liveness gate: verify server is alive before proceeding
if ! curl -sf --max-time 5 "http://127.0.0.1:${LARQL_PORT}/v1/models" >/dev/null 2>&1; then
  echo "error: server not alive on 127.0.0.1:${LARQL_PORT}" >&2
  exit 1
fi

mkdir -p "$OUT_DIR/transcripts"
n=0
while IFS= read -r q || [ -n "$q" ]; do
  [ -z "$q" ] && continue
  n=$((n+1))
  # goose routes to the local openai-compat server ONLY with the full env set:
  # GOOSE_PROVIDER=openai + GOOSE_MODEL select the backend; OPENAI_BASE_URL/_API_KEY
  # point it at our shim. OPENAI_BASE_URL alone silently falls back to the configured
  # cloud provider (found via dogfooding: transcripts showed openrouter/anthropic).
  # shellcheck disable=SC2086
  GOOSE_PROVIDER="openai" GOOSE_MODEL="${GOOSE_MODEL}" \
  OPENAI_BASE_URL="http://127.0.0.1:${LARQL_PORT}/v1" OPENAI_API_KEY="local" \
  OPENAI_HOST="http://127.0.0.1:${LARQL_PORT}" \
    timeout 600 "$GOOSE_BIN" $GOOSE_RUN_ARGS "$q" \
    > "$OUT_DIR/transcripts/${LABEL}-q${n}.txt" 2>&1
  echo "q${n} exit=$? label=${LABEL}"
done < "$QUESTIONS"
echo "done: $n questions -> $OUT_DIR/transcripts (label=${LABEL})"
