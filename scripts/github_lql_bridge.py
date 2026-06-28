"""
github_lql_bridge.py — FastAPI larql-server-protocol bridge for any GitHub repo.

Builds two graph-native vindexes at startup:
  vindex_topo: triples inserted with topological layer assignment (Theory A)
  vindex_spec: triples inserted with spectral layer assignment  (Theory B)

/v1/walk runs both and logs divergence (Jaccard similarity of hit targets).
This divergence is the experimental observable for Theory A vs Theory B.

Run via larql-python environment:
    cd ~/larql/crates/larql-python
    uv run python ~/babel-harness/scripts/github_lql_bridge.py \\
        chrishayuk/larql --port 8383 \\
        --base-vindex ~/larql-vindexes/smollm2-360m.vindex
"""

from __future__ import annotations
import argparse, re, subprocess, sys, time
import pathlib
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query

SCRIPTS_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from graph_vindex import build_graph_vindex

Triple = tuple[str, str, str, float]

_INSERT_RE = re.compile(r'^INSERT\s+"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"', re.MULTILINE)

app = FastAPI()
_state: dict = {}


def _fetch_triples(repo: str, ref: str) -> list[Triple]:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "github_graph.py"),
         "--repo", repo, "--ref", ref, "--output", "lql"],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"github_graph.py failed:\n{result.stderr[:500]}")
    return [(m.group(1), m.group(2), m.group(3), 1.0) for m in _INSERT_RE.finditer(result.stdout)]


def _select_subgraph(triples: list[Triple], max_nodes: int) -> list[Triple]:
    """Return the top-max_nodes triples ranked by subject out-degree.

    Each triple becomes one vindex.insert() call (~0.35s), so this directly
    bounds startup: max_nodes * 0.35s * 2 vindexes ≈ max_nodes * 0.7s.
    Default 100: ~70s build vs ~68 min for the full 5807-triple graph.
    Use --max-nodes 0 for full-graph offline builds.
    """
    if max_nodes <= 0:
        return triples
    out_degree: dict[str, int] = {}
    for s, _, _, _ in triples:
        out_degree[s] = out_degree.get(s, 0) + 1
    return sorted(triples, key=lambda t: -out_degree.get(t[0], 0))[:max_nodes]


def _hit_to_dict(h: object) -> dict:
    return {
        "layer": h.layer,
        "feature": h.feature,
        "gate_score": float(h.gate_score),
        "target": h.target or "",
    }


@app.get("/v1/stats")
async def stats():
    v = _state["vindex_topo"]
    triples = _state["triples"]
    n = v.num_layers
    return {
        "model": f"github://{_state['repo']}",
        "family": "graph",
        "layers": n,
        "features": len(triples),
        "hidden_size": v.hidden_size,
        "dtype": "f32",
        "extract_level": "browse",
        "layer_bands": {
            "syntax":    [0,       n // 3],
            "knowledge": [n // 3,  2 * n // 3],
            "output":    [2 * n // 3, n],
        },
        "loaded": {"browse": True, "inference": False},
        "latency_ms": 0.0,
    }


@app.get("/v1/walk")
async def walk(
    prompt: str = Query(...),
    top: int = Query(5),
    layers: Optional[str] = Query(None),
):
    t0 = time.time()

    layer_list = None
    if layers:
        try:
            start, end = layers.split("-")
            layer_list = list(range(int(start), int(end) + 1))
        except ValueError:
            pass

    topo_hits = _state["vindex_topo"].entity_walk(prompt, layers=layer_list, top_k=top)
    spec_hits = _state["vindex_spec"].entity_walk(prompt, layers=layer_list, top_k=top)

    topo_t = {h.target for h in topo_hits if h.target}
    spec_t = {h.target for h in spec_hits if h.target}
    divergence = {
        "topological_only": sorted(topo_t - spec_t),
        "spectral_only":    sorted(spec_t - topo_t),
        "shared":           sorted(topo_t & spec_t),
        "jaccard": len(topo_t & spec_t) / max(len(topo_t | spec_t), 1),
    }
    _state["divergence_log"].append({"prompt": prompt, **divergence})

    return {
        "hits": [_hit_to_dict(h) for h in topo_hits],
        "divergence": divergence,
        "latency_ms": (time.time() - t0) * 1000,
    }


@app.get("/v1/describe")
async def describe(
    entity: str = Query(...),
    band: str = Query("knowledge"),
    verbose: bool = Query(False),
):
    t0 = time.time()
    hits = _state["vindex_topo"].entity_walk(entity, layers=None, top_k=50)
    edges = [
        {"target": h.target or "", "gate_score": float(h.gate_score),
         "layer": h.layer, "relation": "", "source": "insert", "also": []}
        for h in hits
    ]
    return {"edges": edges, "latency_ms": (time.time() - t0) * 1000}


@app.get("/v1/relations")
async def relations():
    counts: dict[str, int] = {}
    for _, r, _, _ in _state["triples"]:
        counts[r] = counts.get(r, 0) + 1
    return {"relations": [{"name": r, "count": c}
                          for r, c in sorted(counts.items(), key=lambda x: -x[1])]}


@app.get("/v1/select")
async def select(
    entity: Optional[str] = Query(None),
    relation: Optional[str] = Query(None),
    limit: int = Query(20),
):
    results = []
    for s, r, o, c in _state["triples"]:
        if entity and s != entity:
            continue
        if relation and r != relation:
            continue
        results.append({"entity": s, "relation": r, "target": o, "confidence": c})
        if len(results) >= limit:
            break
    return {"edges": results}


@app.get("/v1/divergence-log")
async def divergence_log():
    return {"log": _state["divergence_log"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub LQL Bridge")
    parser.add_argument("repo", help="GitHub repo (owner/repo)")
    parser.add_argument("--port", type=int, default=8383)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--base-vindex", required=True, dest="base_vindex")
    parser.add_argument("--seed", default="",
                        help="Seed entity for topological assignment (default: first triple subject)")
    parser.add_argument("--max-nodes", type=int, default=100, dest="max_nodes",
                        help="Top-N triples by subject out-degree (0=all). Default 100 "
                             "keeps startup under 3 min; use 0 for full-graph offline builds.")
    parser.add_argument("--triples-file", default="", dest="triples_file",
                        help="Read LQL INSERT triples from file instead of calling GitHub API. "
                             "Intended for testing and offline/cached use.")
    args = parser.parse_args()

    if args.triples_file:
        text = pathlib.Path(args.triples_file).read_text()
        pattern = re.compile(r'^INSERT\s+"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"', re.MULTILINE)
        triples = [(m.group(1), m.group(2), m.group(3), 1.0) for m in _INSERT_RE.finditer(text)]
        print(f"  {len(triples)} triples loaded from {args.triples_file}")
    else:
        print(f"Fetching triples: github://{args.repo}@{args.ref}...")
        triples = _fetch_triples(args.repo, args.ref)
        rel_types = len({r for _, r, _, _ in triples})
        print(f"  {len(triples)} triples, {rel_types} relation types")

    if args.max_nodes > 0:
        original_count = len(triples)
        triples = _select_subgraph(triples, args.max_nodes)
        if len(triples) < original_count:
            print(f"  WARNING: graph truncated to top {len(triples)} triples by subject degree"
                  f" ({original_count} total). Pass --max-nodes 0 for the full graph.")
        else:
            print(f"  Subgraph: {len(triples)} triples")

    seed = args.seed or (triples[0][0] if triples else "larql-vindex")

    print(f"Building topological vindex (seed={seed!r})...")
    vindex_topo, _, n = build_graph_vindex(triples, "topological", args.base_vindex, seed=seed)
    print(f"  Inserted {n} triples")

    print("Building spectral vindex...")
    vindex_spec, _, n = build_graph_vindex(triples, "spectral", args.base_vindex)
    print(f"  Inserted {n} triples")

    _state.update({
        "repo": args.repo,
        "triples": triples,
        "vindex_topo": vindex_topo,
        "vindex_spec": vindex_spec,
        "divergence_log": [],
    })

    print(f"Bridge ready: http://localhost:{args.port}")
    print(f'  USE REMOTE "http://localhost:{args.port}" in any LQL session')
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
