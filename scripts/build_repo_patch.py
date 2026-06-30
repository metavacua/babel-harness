#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
build_repo_patch.py — Translate Vindexfile INSERT directives (Form 1) into a
persistent .vlp patch file via the vindex.insert() Python binding.

Background:
  Vindexfile INSERT "e","r","t" is a stub (larql-to-sparql #242) — it writes
  empty gate vectors and produces no retrievable knowledge.  This script works
  around #242 by calling vindex.insert() which computes real gate vectors and
  produces PatchOp::Insert operations visible to WALK queries (entity_walk(),
  WalkFfn gate-KNN scan).

  Why not MODE KNN (LQL Form 2)?  KNN inserts write to knn_store — INFER-only.
  The bridge consumer (github_lql_bridge.py) uses entity_walk() — WALK only —
  so KNN inserts are invisible.

  Why not MODE COMPOSE (LQL Form 3)?  Broken on smollm2-360m: near-zero free
  FFN slot activations (issues #237, #234, #238); alpha_mul calibrated for
  Gemma-3 4B not smollm2.  vindex.insert() is architecture-independent.

Usage:
    python3 scripts/build_repo_patch.py \\
        --vindexfile Vindexfile \\
        --base-vindex ~/larql-vindexes/smollm2-360m.vindex \\
        --output tests/fixtures/babel-harness-94485d4.vlp

    # From a remote repo (uses github_graph.py, GET-only):
    python3 scripts/build_repo_patch.py \\
        --remote chrishayuk/larql@4a120baf \\
        --base-vindex ~/larql-vindexes/smollm2-360m.vindex \\
        --output tests/fixtures/larql-4a120baf.vlp

Insert form terminology (distinct, do not conflate):
  Form 1: INSERT "e","r","t"                                (Vindexfile, stub per #242)
  Form 2: INSERT INTO EDGES (...) VALUES (...) MODE KNN     (LQL session, INFER-only)
  Form 3: INSERT INTO EDGES (...) VALUES (...) MODE COMPOSE (LQL session, broken on smollm2)
  Form 4: BEGIN PATCH / SAVE PATCH / APPLY PATCH / COMPILE  (patch session lifecycle)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

LARQL_PYTHON_DIR = pathlib.Path.home() / "larql/crates/larql-python"
GITHUB_GRAPH_SCRIPT = pathlib.Path(__file__).parent / "github_graph.py"

# Matches: INSERT "entity", "relation", "target"  (Form 1 Vindexfile syntax, per #242)
_FORM1_RE = re.compile(
    r'^INSERT\s+"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"',
    re.MULTILINE,
)

# Inner script executed via `uv run python` from LARQL_PYTHON_DIR (Python 3.12 + larql-python).
# Receives a JSON payload on stdin; writes the .vlp; prints {"status":"ok","num_ops":N} to stdout.
_INNER_SCRIPT = '''\
import base64, json, sys
import numpy as np
import larql

args = json.load(sys.stdin)
base_vindex = args["base_vindex"]
output_vlp = args["output_vlp"]
triples = args["triples"]
created_at = args["created_at"]

vindex = larql.load(base_vindex)

# Workaround for VectorIndex.find_free_feature() mmap/heap split bug:
# The base index reads slot state from the on-disk mmap/heap, which is
# never updated by subsequent set_gate_vector/set_feature_meta calls within
# the same process. So every call to find_free_feature() returns the same slot
# — each new insert silently evicts the previous one.
# PatchedVindex.find_free_feature() tracks the overlay to avoid this; the
# larql-python insert() binding calls the base version and does not.
#
# Fix: pre-scan ALL slot eviction order BEFORE any writes (heap is empty,
# on-disk data is ground truth). Free slots come first; full layers evict
# weakest-c_score first. Consume one slot per insert in that pre-built order.
# Mirrors the overlay logic in larql-vindex/src/patch/overlay.rs:338.
_layer_slot_iters = {}

def _slot_iter_for(layer):
    if layer not in _layer_slot_iters:
        nf = vindex.num_features(layer)
        if nf == 0:
            raise RuntimeError(f"Layer {layer} has no features")
        # Pre-scan before any writes: heap is empty, on-disk data is ground truth
        free_slots = []
        occupied_slots = []  # (feature, c_score)
        for f in range(nf):
            m = vindex.feature_meta(layer, f)
            if m is None:
                free_slots.append(f)
            else:
                occupied_slots.append((f, m.c_score))
        occupied_slots.sort(key=lambda x: x[1])  # weakest-c_score first (eviction order)
        ordered = free_slots + [f for f, _ in occupied_slots]
        _layer_slot_iters[layer] = iter(ordered)
    return _layer_slot_iters[layer]

def _compute_gate_vec(entity, relation, layer):
    """Replicate insert() gate vector synthesis: entity_embed*0.7 + cluster_centre*0.3."""
    entity_embed = np.array(vindex.embed(entity))
    cc = vindex.cluster_centre(relation)
    if cc is not None:
        cc_arr = np.array(cc)
        if len(cc_arr) == len(entity_embed):
            gate_vec = entity_embed * 0.7 + cc_arr * 0.3
        else:
            gate_vec = entity_embed.copy()
    else:
        gate_vec = entity_embed.copy()
    # Normalize to match layer magnitudes (mirrors insert() normalization)
    sample_count = min(vindex.num_features(layer), 100)
    norm_sum, norm_count = 0.0, 0
    for f in range(sample_count):
        gv = vindex.gate_vector(layer, f)
        if gv is not None:
            gv_arr = np.array(gv)
            n = float(np.sqrt(float(np.dot(gv_arr, gv_arr))))
            if n > 0:
                norm_sum += n
                norm_count += 1
    if norm_count > 0:
        avg_norm = norm_sum / norm_count
        my_norm = float(np.sqrt(float(np.dot(gate_vec, gate_vec))))
        if my_norm > 0:
            gate_vec = gate_vec * (avg_norm / my_norm)
    return gate_vec.astype(np.float32)

ops = []
for entity, relation, target in triples:
    layer = vindex.typical_layer(relation)
    if layer is None:
        layer = vindex.num_layers * 3 // 5

    it = _slot_iter_for(layer)
    feature = next(it, None)
    if feature is None:
        raise RuntimeError(f"Exhausted all {vindex.num_features(layer)} slots at layer {layer}")

    gate_vec = _compute_gate_vec(entity, relation, layer)
    vindex.set_gate_vector(layer, feature, gate_vec.tolist())
    vindex.set_feature_meta(layer, feature, target, c_score=1.0)

    gate_b64 = base64.b64encode(gate_vec.tobytes()).decode()
    meta = vindex.feature_meta(layer, feature)
    ops.append({
        "op": "insert",
        "layer": layer,
        "feature": feature,
        "entity": entity,
        "relation": relation,
        "target": target,
        "confidence": 1.0,
        "gate_vector_b64": gate_b64,
        "down_meta": {
            "t": meta.top_token,
            "i": meta.top_token_id,
            "c": float(meta.c_score),
        },
    })

patch = {
    "version": 1,
    "base_model": base_vindex,
    "base_checksum": None,
    "created_at": created_at,
    "operations": ops,
}
with open(output_vlp, "w") as f:
    json.dump(patch, f, indent=2)

print(json.dumps({"status": "ok", "num_ops": len(ops)}))
'''


def parse_vindexfile_inserts(text: str) -> list[tuple[str, str, str]]:
    """Extract (entity, relation, target) triples from Vindexfile Form 1 INSERT lines."""
    return [(m.group(1), m.group(2), m.group(3)) for m in _FORM1_RE.finditer(text)]


def _build_vlp(
    triples: list[tuple[str, str, str]],
    base_vindex: str,
    output_vlp: str,
) -> tuple[int, str]:
    """
    Build a .vlp patch file via vindex.insert() Python binding.

    Spawns a single uv subprocess in LARQL_PYTHON_DIR (Python 3.12 + larql-python).
    For each triple, vindex.insert() computes:
      gate_vec = entity_embed * 0.7 + cluster_centre * 0.3  (normalized)
    and writes it into overrides_gate — visible to WALK (entity_walk / WalkFfn).

    Returns (returncode, combined stdout+stderr).
    """
    args_payload = {
        "base_vindex": base_vindex,
        "output_vlp": output_vlp,
        "triples": triples,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="brp_inner_"
    ) as f:
        f.write(_INNER_SCRIPT)
        inner_path = f.name
    try:
        result = subprocess.run(
            ["uv", "run", "python", inner_path],
            cwd=str(LARQL_PYTHON_DIR),
            input=json.dumps(args_payload),
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        os.unlink(inner_path)


def _fetch_remote_triples(repo_ref: str) -> list[tuple[str, str, str]]:
    """
    Fetch Form-1 INSERT lines from a remote GitHub repo via github_graph.py.
    Uses GET-only GitHub API (no authentication required for public repos).
    repo_ref format: "owner/repo@commit"
    """
    if "@" not in repo_ref:
        raise ValueError(f"--remote must be owner/repo@commit, got: {repo_ref!r}")
    repo, ref = repo_ref.split("@", 1)
    result = subprocess.run(
        [sys.executable, str(GITHUB_GRAPH_SCRIPT), "--repo", repo, "--ref", ref, "--output", "lql"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"github_graph.py failed (exit {result.returncode}):\n{result.stderr}"
        )
    return parse_vindexfile_inserts(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate Vindexfile Form 1 INSERT directives into a .vlp patch file.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--vindexfile",
        metavar="PATH",
        help="Path to a local Vindexfile containing Form 1 INSERT directives.",
    )
    source.add_argument(
        "--remote",
        metavar="OWNER/REPO@REF",
        help="Fetch graph from a GitHub repo at a pinned commit (GET-only, no auth needed).",
    )
    parser.add_argument("--base-vindex", required=True, metavar="PATH", help="Path to base vindex.")
    parser.add_argument("--output", required=True, metavar="PATH", help="Destination .vlp path.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print triples to stdout without loading vindex or writing .vlp.",
    )
    # Accepted but unused — kept for backward compatibility with test harness callers.
    parser.add_argument("--larql-bin", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.vindexfile:
        vf_path = pathlib.Path(args.vindexfile)
        if not vf_path.exists():
            print(f"error: Vindexfile not found: {vf_path}", file=sys.stderr)
            sys.exit(1)
        src = f"vindexfile:{vf_path}"
        triples = parse_vindexfile_inserts(vf_path.read_text())
    else:
        src = f"github:{args.remote}"
        try:
            triples = _fetch_remote_triples(args.remote)
        except (ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    if not triples:
        print(f"error: no INSERT directives found in {src}", file=sys.stderr)
        sys.exit(1)

    output = pathlib.Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        for entity, relation, target in triples:
            print(f'INSERT "{entity}", "{relation}", "{target}"')
        print(f"\n-- {len(triples)} triples would be inserted into {output.name}")
        return

    print(f"build_repo_patch: {len(triples)} triples → {output}", file=sys.stderr)
    rc, out = _build_vlp(triples, args.base_vindex, str(output))
    for line in out.splitlines():
        print(line, file=sys.stderr)

    if rc != 0:
        print(f"error: vindex insert subprocess failed (exit {rc})", file=sys.stderr)
        sys.exit(1)

    if not output.exists():
        print(f"error: subprocess exited 0 but {output} was not created", file=sys.stderr)
        sys.exit(1)

    try:
        patch = json.loads(output.read_text())
        ops = patch.get("operations", [])
    except json.JSONDecodeError as exc:
        print(f"error: output .vlp is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    insert_count = sum(1 for op in ops if op.get("op") == "insert")
    print(
        f"build_repo_patch: ok — {insert_count}/{len(triples)} insert ops in {output.name}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
