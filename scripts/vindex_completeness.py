#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Independent vindex-completeness checker — the REJECTION POWER larql's own
# `verify` lacks. Per metavacua/larql-to-sparql:
#   #155 `larql verify` is checksum-only and *passes a 0-byte / hollow vindex*
#   #201 extract never surfaces skipped tensors / asserts completeness
#   #147 a hollow vindex ships with exit 0 and 0-byte weights
# This asserts a vindex is actually COMPLETE, and exits non-zero (naming the
# specific defect) when it is not. Intended as the correctness gate for the
# babel-harness larql extract-testing bench.
#
# Usage: vindex_completeness.py <vindex-dir> [--no-checksum]
# Exit 0 = complete; non-zero = incomplete/hollow.
import json, sys, hashlib, os

if len(sys.argv) < 2:
    print("usage: vindex_completeness.py <vindex-dir> [--no-checksum]"); sys.exit(2)
vindex = sys.argv[1]
do_checksum = "--no-checksum" not in sys.argv[2:]
def p(f): return os.path.join(vindex, f)
fails = []

# 1. index.json present + parseable
try:
    idx = json.load(open(p("index.json")))
except Exception as e:
    print(f"FAIL: index.json unreadable ({e}) — not a vindex"); sys.exit(1)

checksums = idx.get("checksums", {}) or {}
if not checksums:
    fails.append("index.json has NO checksums — larql verify would be a no-op here (#155)")

# 2. every checksummed file exists, is NON-ZERO, and (optionally) matches its checksum
for fname, expected in checksums.items():
    fp = p(fname)
    if not os.path.exists(fp):
        fails.append(f"missing file listed in checksums: {fname}"); continue
    if os.path.getsize(fp) == 0:
        fails.append(f"HOLLOW (0-byte) file: {fname}"); continue
    if do_checksum:
        h = hashlib.sha256()
        with open(fp, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != expected:
            fails.append(f"checksum mismatch: {fname}")

# 3. has_model_weights ⟹ the core weight bins must be present + non-zero
if idx.get("has_model_weights"):
    for f in ("embeddings.bin", "norms.bin", "gate_vectors.bin", "up_weights.bin", "down_weights.bin"):
        fp = p(f)
        if not (os.path.exists(fp) and os.path.getsize(fp) > 0):
            fails.append(f"has_model_weights=True but core weight file missing/empty: {f}")

# 4. weight_manifest tensor count vs num_layers — a wildly-short manifest = truncated extract (#201)
nlayers = idx.get("num_layers", 0) or 0
try:
    man = json.load(open(p("weight_manifest.json")))
    ntensors = len(man) if isinstance(man, list) else len(man.get("tensors", []))
    if ntensors == 0:
        fails.append("weight_manifest.json lists 0 tensors")
    elif nlayers and ntensors < nlayers:
        fails.append(f"weight_manifest has only {ntensors} tensors for {nlayers} layers — truncated extract?")
except FileNotFoundError:
    ntensors = None  # manifest optional at browse level
except Exception as e:
    fails.append(f"weight_manifest.json unreadable: {e}")

model = idx.get("model", "?"); lvl = idx.get("extract_level", "?")
if fails:
    print(f"FAIL: vindex INCOMPLETE ({lvl} extract, {os.path.basename(vindex)}):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print(f"PASS: vindex complete — {lvl} extract, {nlayers} layers, "
      f"{len(checksums)} files checksummed+non-zero"
      + (f", {ntensors} tensors" if 'ntensors' in dir() and ntensors else ""))
