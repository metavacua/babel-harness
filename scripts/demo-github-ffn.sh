#!/usr/bin/env bash
# demo-github-ffn.sh — Treat chrishayuk/larql as an FFN knowledge base.
#
# Demonstrates:
#   1. Bridge starts, builds topological + spectral vindexes
#   2. LQL USE REMOTE session queries the bridge
#   3. coding-agent completes a real task using bridge graph context
#   4. Divergence log printed (Theory A vs Theory B observable)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LARQL_PY_DIR="$HOME/larql/crates/larql-python"
LARQL_BIN="${LARQL_BIN:-$HOME/larql/target/release/larql}"
BASE_VINDEX="${BASE_VINDEX:-$HOME/larql-vindexes/smollm2-360m.vindex}"
BRIDGE_PORT="${BRIDGE_PORT:-8383}"
BRIDGE_URL="http://localhost:$BRIDGE_PORT"

if [[ ! -d "$BASE_VINDEX" ]]; then
  echo "ERROR: base vindex not found: $BASE_VINDEX" >&2
  echo "Set BASE_VINDEX to a full larql vindex directory." >&2
  exit 1
fi

echo "=== GitHub-as-FFN Bridge Demo ==="
echo "  repo: chrishayuk/larql"
echo "  base vindex: $(basename "$BASE_VINDEX")"
echo ""

# ── Start bridge ──────────────────────────────────────────────────────────────
echo "Starting bridge (fetches GitHub triples + builds two vindexes)..."
cd "$LARQL_PY_DIR"
uv run python "$REPO/scripts/github_lql_bridge.py" \
  chrishayuk/larql \
  --port "$BRIDGE_PORT" \
  --base-vindex "$BASE_VINDEX" \
  --ref main &
BRIDGE_PID=$!
trap "kill $BRIDGE_PID 2>/dev/null; wait $BRIDGE_PID 2>/dev/null || true" EXIT

printf "Waiting for bridge"
for i in $(seq 1 120); do
  curl -sf "$BRIDGE_URL/v1/stats" >/dev/null 2>&1 && echo " ready." && break
  printf "."
  sleep 1
done

STATS=$(curl -sf "$BRIDGE_URL/v1/stats")
LAYERS=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['layers'])")
FEATURES=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin)['features'])")
echo "  $FEATURES triples across $LAYERS layers"

# ── WalkFfn queries (both modes) ──────────────────────────────────────────────
echo ""
echo "=== WalkFfn Queries (topological primary, spectral divergence logged) ==="
for QUERY in "gate_knn" "entity_walk" "vindex insert" "Laplacian eigenvector"; do
  RESULT=$(curl -sf "$BRIDGE_URL/v1/walk?prompt=$(python3 -c \
    "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$QUERY")&top=5")
  JACCARD=$(echo "$RESULT" | python3 -c \
    "import sys,json; print(f\"{json.load(sys.stdin)['divergence']['jaccard']:.3f}\")")
  HITS=$(echo "$RESULT" | python3 -c \
    "import sys,json; hits=json.load(sys.stdin)['hits']; print(', '.join(h['target'] for h in hits[:3] if h['target']) or '(none)')")
  echo "  WALK \"$QUERY\" → [$HITS]  Jaccard=$JACCARD"
done

# ── LQL USE REMOTE session ────────────────────────────────────────────────────
if [[ -x "$LARQL_BIN" ]]; then
  echo ""
  echo "=== LQL Session via USE REMOTE ==="
  "$LARQL_BIN" lql "$(printf 'USE REMOTE "%s"; WALK "gate_knn" TOP 5;' "$BRIDGE_URL")"
fi

# ── coding-agent with bridge context ─────────────────────────────────────────
echo ""
echo "=== Coding Agent Task (GITHUB_LQL_BRIDGE_URL set) ==="
DEMO_TASK="Describe in 3 sentences how entity_walk in larql-vindex implements the WalkFfn gate-KNN mechanism, citing the key function names."
export PATH="$REPO/bin:$PATH"
GITHUB_LQL_BRIDGE_URL="$BRIDGE_URL" \
  coding-agent "$DEMO_TASK" 2>&1 | tail -30

# ── Divergence log summary ────────────────────────────────────────────────────
echo ""
echo "=== Theory A vs B Divergence Summary ==="
curl -sf "$BRIDGE_URL/v1/divergence-log" | python3 - <<'PY'
import sys, json
log = json.load(sys.stdin)["log"]
print(f"  {len(log)} queries logged")
jaccards = [e["jaccard"] for e in log]
if jaccards:
    print(f"  Jaccard range: {min(jaccards):.3f} – {max(jaccards):.3f}")
    low = [e for e in log if e["jaccard"] < 0.5]
    if low:
        print("  High-divergence queries (Theory A ≠ Theory B):")
        for e in low:
            print(f'    "{e["prompt"]}"')
            print(f'      topo-only: {e["topological_only"][:3]}')
            print(f'      spec-only: {e["spectral_only"][:3]}')
    else:
        print("  All queries have Jaccard ≥ 0.5 (theories largely agree)")
PY

echo ""
echo "Demo complete. Bridge divergence log: GET $BRIDGE_URL/v1/divergence-log"
