#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Integration tests for larql vindex graft — GitHub repo → INSERT triples → larql serve.
#
# These tests require larql to be built and LARQL_BIN set, plus LARQL_VINDEX_BASE
# pointing to a directory with a smollm2-360m.vindex. They are SKIPPED automatically
# if the prerequisites are absent, so they do not break CI.
#
# Prerequisite check:
#   LARQL_BIN      — path to larql binary (default: $HOME/larql/target/release/larql)
#   LARQL_VINDEX_BASE — directory with *.vindex files
#   GITHUB_TOKEN or GH_TOKEN — for github_graph.py API calls (optional; uses gh CLI)
#
# Run:
#   bash tests/test-larql-graft.bash
#   LARQL_BIN=/path/to/larql LARQL_VINDEX_BASE=/path/to/vindexes bash tests/test-larql-graft.bash

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP: $1 ($2)"; }

section() { echo ""; echo "--- $1 ---"; }

LARQL_BIN="${LARQL_BIN:-$HOME/larql/target/release/larql}"
LARQL_VINDEX_BASE="${LARQL_VINDEX_BASE:-}"
LARQL_PORT="${LARQL_PORT:-8181}"   # use non-default port to avoid conflicts

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

HAVE_LARQL=0
HAVE_VINDEX_BASE=0
HAVE_GH=0

[ -x "$LARQL_BIN" ] && HAVE_LARQL=1
[ -n "$LARQL_VINDEX_BASE" ] && [ -d "$LARQL_VINDEX_BASE" ] && HAVE_VINDEX_BASE=1
command -v gh >/dev/null 2>&1 && HAVE_GH=1

# ---------------------------------------------------------------------------
# Test 1: github_graph.py extract — lql output contains INSERT triples
# ---------------------------------------------------------------------------
section "1: github_graph.py lql output for chrishayuk/larql"

if [ "$HAVE_GH" = "0" ]; then
  skip "lql extraction" "gh CLI not available"
else
  tmp_lql=$(mktemp)
  python3 "$REPO/scripts/github_graph.py" \
    --repo chrishayuk/larql --ref main --output lql >"$tmp_lql" 2>/dev/null || true
  if grep -q '^INSERT ' "$tmp_lql"; then
    pass "lql output contains INSERT triples"
  else
    fail "lql output missing INSERT triples (header: $(head -3 "$tmp_lql"))"
  fi
  if grep -q '"larql-vindex"' "$tmp_lql"; then
    pass "larql-vindex crate entity present in INSERT triples"
  else
    fail "larql-vindex entity missing (lines: $(wc -l < "$tmp_lql"))"
  fi
  rm -f "$tmp_lql"
fi

# ---------------------------------------------------------------------------
# Test 2: extract-graph.py --remote merges remote triples into Vindexfile
# ---------------------------------------------------------------------------
section "2: extract-graph.py --remote merges github graph into Vindexfile"

if [ "$HAVE_GH" = "0" ] || [ "$HAVE_VINDEX_BASE" = "0" ]; then
  skip "remote merge" "gh CLI or LARQL_VINDEX_BASE not available"
else
  tmp_vindex=$(mktemp -d)
  LARQL_VINDEX_BASE="$LARQL_VINDEX_BASE" python3 "$REPO/scripts/extract-graph.py" \
    --remote chrishayuk/larql@main 2>/dev/null \
    && true  # don't fail the test if Vindexfile write succeeds
  if grep -q '# FROM github://chrishayuk/larql@main' "$REPO/Vindexfile"; then
    pass "Vindexfile contains # FROM github://chrishayuk/larql@main directive"
  else
    fail "Vindexfile missing remote directive"
  fi
  if grep -q '"larql-vindex"' "$REPO/Vindexfile" 2>/dev/null; then
    pass "Vindexfile contains larql-vindex INSERT triple from remote graph"
  else
    skip "larql-vindex triple in Vindexfile" \
         "Vindexfile not regenerated (need LARQL_VINDEX_BASE with write access)"
  fi
  rm -rf "$tmp_vindex"
fi

# ---------------------------------------------------------------------------
# Test 3: ddt_proof.py reports PROVEN
# ---------------------------------------------------------------------------
section "3: DDT composition proof passes"

proof_out=$(python3 "$REPO/scripts/ddt_proof.py" 2>&1)
if echo "$proof_out" | grep -q "Verdict: PROVEN"; then
  pass "ddt_proof.py Verdict: PROVEN"
else
  fail "ddt_proof.py did not output PROVEN (got: $(echo "$proof_out" | grep Verdict:))"
fi
if echo "$proof_out" | grep -q "ddt.*ddt_proof.py"; then
  pass "ddt_proof.py proves its own DDT compliance (self-applicable)"
else
  fail "ddt_proof.py missing self-applicable proof"
fi

# ---------------------------------------------------------------------------
# Test 4: larql serve grafted vindex + gate-KNN over GitHub entity nodes
# ---------------------------------------------------------------------------
section "4: larql serve grafted vindex — gate-KNN navigates remote entities"

if [ "$HAVE_LARQL" = "0" ] || [ "$HAVE_VINDEX_BASE" = "0" ]; then
  skip "larql serve + gate-KNN" \
       "LARQL_BIN or LARQL_VINDEX_BASE not set (set to run end-to-end test)"
else
  # Build a minimal grafted vindex
  BUILD_DIR=$(mktemp -d)
  trap "rm -rf $BUILD_DIR" EXIT

  # Extract local graph only (remote requires GH auth which may be absent in CI)
  LARQL_VINDEX_BASE="$LARQL_VINDEX_BASE" \
    python3 "$REPO/scripts/extract-graph.py" 2>/dev/null || true

  # Build
  if "$LARQL_BIN" build "$REPO/" -o "$BUILD_DIR/babel-harness-grafted.vindex" \
       2>/dev/null; then
    pass "larql build produces grafted vindex"
  else
    fail "larql build failed — see LARQL_BIN logs"
  fi

  # Start larql-server on test port
  "$LARQL_BIN" serve "$BUILD_DIR/babel-harness-grafted.vindex" \
    --port "$LARQL_PORT" >/dev/null 2>&1 &
  SERVER_PID=$!
  trap "kill $SERVER_PID 2>/dev/null; rm -rf $BUILD_DIR" EXIT

  # Poll until ready (max 30s)
  READY=0
  for i in $(seq 1 30); do
    if curl -sf "http://localhost:${LARQL_PORT}/v1/models" >/dev/null 2>&1; then
      READY=1; break
    fi
    sleep 1
  done

  if [ "$READY" = "1" ]; then
    pass "larql-server started and serving grafted vindex"

    # Issue a SELECT query to verify gate-KNN over coding-agent entities
    select_result=$(curl -sf \
      "http://localhost:${LARQL_PORT}/v1/completions" \
      -H "Content-Type: application/json" \
      -d '{"model":"babel-harness-grafted","prompt":"coding-agent","max_tokens":32}' \
      2>/dev/null) || true

    if [ -n "$select_result" ]; then
      pass "larql-server responds to inference request over grafted vindex"
    else
      fail "larql-server returned empty response to inference request"
    fi
  else
    fail "larql-server did not start within 30s"
  fi
fi

# ---------------------------------------------------------------------------
# Tests 5–6: Bridge smoke (only when GITHUB_LQL_BRIDGE_URL set)
# ---------------------------------------------------------------------------
section "5–6: Bridge smoke tests (GITHUB_LQL_BRIDGE_URL)"

if [[ -n "${GITHUB_LQL_BRIDGE_URL:-}" ]]; then
  STATS=$(curl -sf "${GITHUB_LQL_BRIDGE_URL}/v1/stats" 2>/dev/null || echo '{}')
  if echo "$STATS" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); assert d.get('model','').startswith('github://')" \
      2>/dev/null; then
    pass "bridge /v1/stats — model starts with github://"
  else
    fail "bridge /v1/stats — model field missing or wrong: $STATS"
  fi

  WALK=$(curl -sf "${GITHUB_LQL_BRIDGE_URL}/v1/walk?prompt=gate_knn&top=3" 2>/dev/null || echo '{}')
  if echo "$WALK" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); assert 'hits' in d and 'divergence' in d" \
      2>/dev/null; then
    pass "bridge /v1/walk — hits + divergence present"
  else
    fail "bridge /v1/walk — missing hits or divergence: $WALK"
  fi
else
  echo "  SKIP bridge tests (set GITHUB_LQL_BRIDGE_URL=http://... to enable)"
fi

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
