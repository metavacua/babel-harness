#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Tests for scripts/vindex_completeness.py — the independent rejection-power
# checker. Proves it DETECTS the hollow/incomplete vindexes larql verify passes.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHK="$ROOT/scripts/vindex_completeness.py"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }
bad(){ echo "  FAIL: $1"; ((FAIL++))||true; }

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# --- 1. HOLLOW: a 0-byte weight file the checksums reference → must FAIL ---
h="$tmp/hollow.vindex"; mkdir -p "$h"
: > "$h/data.bin"   # 0 bytes
printf '{"checksums":{"data.bin":"deadbeef"},"num_layers":2,"has_model_weights":false}' > "$h/index.json"
if python3 "$CHK" "$h" >/tmp/vc.out 2>&1; then bad "hollow (0-byte) vindex should be rejected"; else
  grep -q "HOLLOW" /tmp/vc.out && ok "rejects hollow 0-byte file (larql verify passes this — #155)" || bad "rejected but not as HOLLOW"
fi

# --- 2. MISSING: checksums reference a file that isn't there → must FAIL ---
m="$tmp/missing.vindex"; mkdir -p "$m"
printf '{"checksums":{"gone.bin":"deadbeef"},"num_layers":2,"has_model_weights":false}' > "$m/index.json"
python3 "$CHK" "$m" >/dev/null 2>&1 && bad "missing-file vindex should be rejected" || ok "rejects missing checksummed file"

# --- 3. TRUNCATED weights: has_model_weights but core bins absent → must FAIL ---
t="$tmp/truncated.vindex"; mkdir -p "$t"; printf 'x' > "$t/data.bin"
sum=$(sha256sum "$t/data.bin" | cut -d' ' -f1)
printf '{"checksums":{"data.bin":"%s"},"num_layers":2,"has_model_weights":true}' "$sum" > "$t/index.json"
python3 "$CHK" "$t" >/dev/null 2>&1 && bad "has_model_weights w/ no core bins should be rejected" || ok "rejects has_model_weights vindex missing core weight bins"

# --- 4. COMPLETE mini vindex (matching checksum, non-zero, manifest) → must PASS ---
c="$tmp/complete.vindex"; mkdir -p "$c"; printf 'realdata' > "$c/data.bin"
sum=$(sha256sum "$c/data.bin" | cut -d' ' -f1)
printf '{"checksums":{"data.bin":"%s"},"num_layers":2,"has_model_weights":false,"extract_level":"browse"}' "$sum" > "$c/index.json"
python3 -c "import json; json.dump([{'key':f'l{i}'} for i in range(20)], open('$c/weight_manifest.json','w'))"
python3 "$CHK" "$c" >/tmp/vc.out 2>&1 && ok "accepts a complete vindex" || { bad "complete vindex wrongly rejected"; cat /tmp/vc.out; }

# --- 5. REAL vindex on disk (fast, no checksum) → must PASS if present ---
RV="$HOME/larql-vindexes/qwen3-0.6b.vindex"
if [ -d "$RV" ]; then
  python3 "$CHK" "$RV" --no-checksum >/tmp/vc.out 2>&1 && ok "accepts the real qwen3-0.6b.vindex" || { bad "real vindex rejected"; cat /tmp/vc.out; }
else
  echo "  SKIP: real vindex not present"
fi

# --- 6. Q4K layout: packed weights (interleaved_kquant, no up/down.bin) → must PASS (was a false positive) ---
q="$tmp/q4k.vindex"; mkdir -p "$q"
for f in embeddings.bin norms.bin attn_weights_kquant.bin interleaved_kquant.bin; do printf 'w' > "$q/$f"; done
python3 -c "import json; json.dump([{'key':f'l{i}'} for i in range(20)], open('$q/weight_manifest.json','w'))"
sum=$(sha256sum "$q/norms.bin" | cut -d' ' -f1)
printf '{"checksums":{"norms.bin":"%s"},"num_layers":2,"has_model_weights":true,"quant":"q4k","extract_level":"all"}' "$sum" > "$q/index.json"
python3 "$CHK" "$q" >/tmp/vc.out 2>&1 && ok "accepts a valid Q4K-layout vindex (interleaved_kquant, no up/down.bin)" || { bad "q4k vindex wrongly rejected"; cat /tmp/vc.out; }

echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
