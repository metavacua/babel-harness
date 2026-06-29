# GitHub-as-FFN Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bridge that serves any GitHub repository as a larql-server-compatible HTTP endpoint so `USE REMOTE "http://..."` in any LQL session treats the repo's code graph as the WalkFfn knowledge base of a language model — with no base model representation space required.

**Architecture:** Three components: (1) `scripts/graph_vindex.py` — pure Python module computing formal topological and spectral layer assignments from a triple list, then populating a larql vindex via `vindex.insert()` with explicit layer parameters; (2) `scripts/github_lql_bridge.py` — FastAPI server implementing the larql-server HTTP protocol (`/v1/stats`, `/v1/walk`, `/v1/describe`, `/v1/relations`, `/v1/select`), building both a topological and spectral vindex at startup and logging divergence between WalkFfn outputs on every `/v1/walk` call; (3) `bin/coding-agent` modification adding `GITHUB_LQL_BRIDGE_URL` support to inject walk results as task context. The divergence between topological and spectral WalkFfn outputs is the primary experimental observable, settling Theory A (WalkFfn ≡ weighted BFS) vs Theory B (WalkFfn encodes global spectral structure).

**Tech Stack:** Python 3.11+, scipy (Laplacian eigendecomposition via `linalg.eigh`), networkx (BFS distances, Laplacian), numpy, FastAPI, uvicorn, larql-python (PyO3 bindings from `~/larql/crates/larql-python`), `scripts/github_graph.py` (existing, `--output lql` mode)

## Global Constraints

- All commits and PRs only in `metavacua/babel-harness` — `chrishayuk/larql` is GET-only (DDT security precondition: `GitHubAPI[POST/PATCH/DELETE, non-metavacua]` is a ToolCall precondition failure)
- Python ≥ 3.11
- larql-python is not installed system-wide; all scripts requiring it run via `cd ~/larql/crates/larql-python && uv run python SCRIPT`
- Base vindex for embedding space: `~/larql-vindexes/smollm2-360m.vindex` (full vindex with `attn_weights.bin`, not a browse-level slice)
- No changes to `~/larql/` codebase (read-only upstream repository)
- `vindex.insert(entity, relation, target, layer=L, confidence=C)` with explicit `layer=` is the only write path into the vindex
- Topological assignment: `l(v) = floor(d(v,seed)/D × num_layers)`, clipped to `[0, num_layers-1]`; `l(r) = round(mean((l(u)+l(v))/2) for (u,r,v) ∈ E_r)` — Theory A
- Spectral assignment: `l(v) = argmax_l Σ_{λ_i ∈ B_l} |x̂_v(i)|²` where B_l partitions the non-trivial eigenvalue range; same `l(r)` formula — Theory B
- Unreachable nodes (topological) get fallback layer `num_layers // 2`

---

## Prerequisite: Build larql-python (run once per machine)

```bash
cd ~/larql/crates/larql-python
uv sync --no-install-project --group dev
uv run maturin develop --release
uv pip install scipy networkx fastapi uvicorn httpx pytest
# Verify:
uv run python -c "import larql; print('larql ok')"
uv run python -c "import scipy, networkx, fastapi; print('deps ok')"
```

Expected: both print `ok`. If maturin fails, run `cd ~/larql && cargo build --release -p larql-vindex` first.

---

### Task 1: Graph-Native Vindex Constructor with Dual Layer Assignment

**Files:**
- Create: `scripts/graph_vindex.py`
- Create: `tests/test_graph_vindex.py`

**Interfaces:**
- Consumes: list of `Triple = tuple[str, str, str, float]` — `(subject, relation, object, confidence)`
- Produces:
  - `compute_topological_assignment(triples: list[Triple], num_layers: int, seed: str) -> dict[str, int]` — layer index for each node entity and relation type
  - `compute_spectral_assignment(triples: list[Triple], num_layers: int) -> dict[str, int]` — layer index for each node entity and relation type
  - `build_graph_vindex(triples: list[Triple], mode: Literal["topological","spectral"], base_vindex_path: str, seed: str = "") -> tuple[larql.Vindex, dict[str, int], int]` — mutated vindex with graph gate vectors inserted at assigned layers, the layer map, and insert count

- [ ] **Step 1: Write failing tests**

Create `tests/test_graph_vindex.py`:

```python
"""Tests for graph_vindex.py — pure graph math, no larql required."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

from graph_vindex import compute_topological_assignment, compute_spectral_assignment

TRIPLES = [
    ("A", "calls", "B", 1.0),
    ("B", "calls", "C", 1.0),
    ("A", "imports", "D", 1.0),
]


def test_topological_seed_is_layer_zero():
    m = compute_topological_assignment(TRIPLES, num_layers=8, seed="A")
    assert m["A"] == 0, f"seed should be layer 0, got {m['A']}"


def test_topological_ordering_preserved():
    m = compute_topological_assignment(TRIPLES, num_layers=8, seed="A")
    assert m["B"] >= m["A"]
    assert m["C"] >= m["B"]


def test_topological_relation_layer_in_range():
    m = compute_topological_assignment(TRIPLES, num_layers=8, seed="A")
    assert 0 <= m["calls"] <= 7
    assert 0 <= m["imports"] <= 7


def test_topological_all_in_range():
    m = compute_topological_assignment(TRIPLES, num_layers=8, seed="A")
    assert all(0 <= v <= 7 for v in m.values()), str(m)


def test_topological_unreachable_gets_fallback():
    triples = [("A", "calls", "B", 1.0), ("X", "calls", "Y", 1.0)]
    m = compute_topological_assignment(triples, num_layers=8, seed="A")
    assert m["X"] == 4  # fallback = num_layers // 2


def test_spectral_all_in_range():
    m = compute_spectral_assignment(TRIPLES, num_layers=8)
    assert all(0 <= v <= 7 for v in m.values()), str(m)


def test_spectral_covers_all_nodes_and_relations():
    m = compute_spectral_assignment(TRIPLES, num_layers=8)
    for e in ["A", "B", "C", "D", "calls", "imports"]:
        assert e in m, f"missing: {e}"


def test_spectral_single_node():
    m = compute_spectral_assignment([("A", "self", "A", 1.0)], num_layers=4)
    assert "A" in m and 0 <= m["A"] <= 3


def test_assignments_cover_same_keys():
    """Both modes must cover the same entities — divergence is in layer values, not keys."""
    t = compute_topological_assignment(TRIPLES, num_layers=8, seed="A")
    s = compute_spectral_assignment(TRIPLES, num_layers=8)
    assert set(t.keys()) == set(s.keys())
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/larql/crates/larql-python
uv run pytest ~/babel-harness/tests/test_graph_vindex.py -v 2>&1 | head -15
```
Expected: `ModuleNotFoundError: No module named 'graph_vindex'`

- [ ] **Step 3: Write `scripts/graph_vindex.py`**

Create `scripts/graph_vindex.py`:

```python
"""
graph_vindex.py — Graph-native larql vindex construction with dual layer assignment.

Two formally derived modes:
  topological: l(v) = floor(d(v, seed) / D * num_layers)   [Theory A — WalkFfn ≡ weighted BFS]
  spectral:    l(v) = argmax_l sum|x̂_v(i)|² for λ_i in band B_l   [Theory B — spectral structure]
  both:        l(r) = round(mean((l(u)+l(v))/2) for (u,r,v) in E_r)

Divergence between topological and spectral WalkFfn outputs is the experimental
observable that settles Theory A vs Theory B.
"""

from __future__ import annotations
from typing import Literal
import numpy as np
import networkx as nx
from scipy import linalg

Triple = tuple[str, str, str, float]  # (subject, relation, object, confidence)


def compute_topological_assignment(
    triples: list[Triple],
    num_layers: int,
    seed: str,
) -> dict[str, int]:
    """Return layer index for each node and relation type via BFS hop-distance."""
    G = nx.DiGraph()
    for s, r, o, _ in triples:
        G.add_edge(s, o)

    try:
        distances = nx.single_source_shortest_path_length(G, seed)
    except nx.NetworkXError:
        distances = {}

    D = max(distances.values()) if distances else 1
    fallback = num_layers // 2

    node_layers: dict[str, int] = {}
    for v in G.nodes():
        d = distances.get(v)
        if d is not None:
            node_layers[v] = min(int(d / D * num_layers), num_layers - 1)
        else:
            node_layers[v] = fallback

    rel_acc: dict[str, list[float]] = {}
    for s, r, o, _ in triples:
        ls = node_layers.get(s, fallback)
        lo = node_layers.get(o, fallback)
        rel_acc.setdefault(r, []).append((ls + lo) / 2)

    relation_layers = {r: round(sum(v) / len(v)) for r, v in rel_acc.items()}
    return {**node_layers, **relation_layers}


def compute_spectral_assignment(
    triples: list[Triple],
    num_layers: int,
) -> dict[str, int]:
    """Return layer index for each node and relation type via Laplacian eigenvalue bands."""
    G = nx.DiGraph()
    for s, r, o, _ in triples:
        G.add_edge(s, o)

    nodes = list(G.nodes())
    if not nodes:
        return {}

    L = nx.normalized_laplacian_matrix(G.to_undirected(), nodelist=nodes).toarray()
    eigenvalues, eigenvectors = linalg.eigh(L)  # ascending order

    nontrivial = eigenvalues > 1e-10
    if not nontrivial.any():
        return {v: 0 for v in nodes}

    lambda_min = float(eigenvalues[nontrivial][0])
    lambda_max = float(eigenvalues[-1])
    band_edges = np.linspace(lambda_min, lambda_max, num_layers + 1)

    node_layers: dict[str, int] = {}
    for i, v in enumerate(nodes):
        x_v = eigenvectors[i]
        band_energies = np.zeros(num_layers)
        for l in range(num_layers):
            lo = band_edges[l]
            hi = band_edges[l + 1] if l < num_layers - 1 else float("inf")
            mask = (eigenvalues >= lo) & (eigenvalues < hi)
            band_energies[l] = float(np.sum(x_v[mask] ** 2))
        node_layers[v] = int(np.argmax(band_energies))

    fallback = num_layers // 2
    rel_acc: dict[str, list[float]] = {}
    for s, r, o, _ in triples:
        ls = node_layers.get(s, fallback)
        lo = node_layers.get(o, fallback)
        rel_acc.setdefault(r, []).append((ls + lo) / 2)

    relation_layers = {r: round(sum(v) / len(v)) for r, v in rel_acc.items()}
    return {**node_layers, **relation_layers}


def build_graph_vindex(
    triples: list[Triple],
    mode: Literal["topological", "spectral"],
    base_vindex_path: str,
    seed: str = "",
) -> tuple[object, dict[str, int], int]:
    """
    Load base vindex, insert all triples at formally assigned layers.

    Returns (vindex, layer_map, insert_count).
    Call larql.load(path) twice to get independent topo + spectral vindex objects.
    """
    import larql

    vindex = larql.load(base_vindex_path)
    num_layers = vindex.num_layers

    if mode == "topological":
        if not seed:
            raise ValueError("topological mode requires a seed entity")
        layer_map = compute_topological_assignment(triples, num_layers, seed)
    elif mode == "spectral":
        layer_map = compute_spectral_assignment(triples, num_layers)
    else:
        raise ValueError(f"unknown mode: {mode!r} — use 'topological' or 'spectral'")

    fallback = num_layers // 2
    inserted = 0
    for s, r, o, conf in triples:
        layer = layer_map.get(r, fallback)
        vindex.insert(s, r, o, layer=layer, confidence=float(conf))
        inserted += 1

    return vindex, layer_map, inserted
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd ~/larql/crates/larql-python
uv run pytest ~/babel-harness/tests/test_graph_vindex.py -v
```
Expected: `9 passed` in under 5 seconds.

- [ ] **Step 5: Commit**

```bash
cd ~/babel-harness
git add scripts/graph_vindex.py tests/test_graph_vindex.py
git commit -m "feat: graph_vindex.py — topological + spectral layer assignment for graph-native vindex"
```

---

### Task 2: GitHub LQL Bridge Server

**Files:**
- Create: `scripts/github_lql_bridge.py`
- Create: `tests/test_github_lql_bridge.py`

**Interfaces:**
- Consumes: `build_graph_vindex(triples, mode, base_vindex_path, seed)` from Task 1
- Consumes: `scripts/github_graph.py --repo OWNER/REPO --ref REF --output lql` stdout — INSERT lines in format `INSERT "s", "r", "o"`
- Produces: HTTP server implementing the larql-server protocol:
  - `GET /v1/stats` → `{"model": "github://REPO", "family": "graph", "layers": N, "features": N, "hidden_size": N, "dtype": "f32", "extract_level": "browse", "layer_bands": {...}, "loaded": {"browse": true, "inference": false}, "latency_ms": N}`
  - `GET /v1/walk?prompt=X&top=N&layers=start-end` → `{"hits": [{"layer", "feature", "gate_score", "target"}], "divergence": {"topological_only", "spectral_only", "shared", "jaccard"}, "latency_ms": N}`
  - `GET /v1/describe?entity=X&band=knowledge&verbose=false` → `{"edges": [{"target", "gate_score", "layer", "relation", "source", "also"}], "latency_ms": N}`
  - `GET /v1/relations` → `{"relations": [{"name", "count"}]}`
  - `GET /v1/select?entity=X&relation=R&limit=20` → `{"edges": [{"entity", "relation", "target", "confidence"}]}`
  - `GET /v1/divergence-log` → `{"log": [{"prompt", "topological_only", "spectral_only", "shared", "jaccard"}]}`

- [ ] **Step 1: Write failing integration tests**

Create `tests/test_github_lql_bridge.py`:

```python
"""Integration tests for github_lql_bridge.py.

Requires smollm2-360m.vindex and network access. Skips automatically if unavailable.
First run takes 3-5 minutes (GitHub fetch + two vindex builds).
"""
import pathlib, subprocess, sys, time
import pytest

REPO = pathlib.Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
LARQL_PY_DIR = pathlib.Path.home() / "larql/crates/larql-python"
BASE_VINDEX = pathlib.Path.home() / "larql-vindexes/smollm2-360m.vindex"
BRIDGE_PORT = 18383


@pytest.fixture(scope="module")
def bridge_url():
    if not BASE_VINDEX.exists():
        pytest.skip("smollm2-360m.vindex not available")

    proc = subprocess.Popen(
        ["uv", "run", "python", str(SCRIPTS / "github_lql_bridge.py"),
         "chrishayuk/larql", "--port", str(BRIDGE_PORT),
         "--base-vindex", str(BASE_VINDEX), "--ref", "main"],
        cwd=LARQL_PY_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    import httpx
    for _ in range(180):
        try:
            r = httpx.get(f"http://localhost:{BRIDGE_PORT}/v1/stats", timeout=2)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=5)
        pytest.fail(f"bridge did not start within 180s\nstdout: {out[-500:]}\nstderr: {err[-500:]}")

    yield f"http://localhost:{BRIDGE_PORT}"
    proc.terminate()
    proc.wait(timeout=5)


def test_stats_model_is_github(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["model"].startswith("github://")
    assert d["layers"] > 0
    assert d["features"] > 0


def test_walk_returns_hits_and_divergence(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/walk", params={"prompt": "gate_knn", "top": 5})
    assert r.status_code == 200
    d = r.json()
    assert "hits" in d and isinstance(d["hits"], list)
    assert "divergence" in d
    assert 0.0 <= d["divergence"]["jaccard"] <= 1.0


def test_walk_divergence_log_accumulates(bridge_url):
    import httpx
    httpx.get(f"{bridge_url}/v1/walk", params={"prompt": "entity_walk", "top": 3})
    r = httpx.get(f"{bridge_url}/v1/divergence-log")
    assert r.status_code == 200
    log = r.json()["log"]
    assert len(log) >= 1
    assert "jaccard" in log[-1]


def test_describe_returns_edges(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/describe", params={"entity": "larql-vindex"})
    assert r.status_code == 200
    assert "edges" in r.json()


def test_relations_nonempty(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/relations")
    assert r.status_code == 200
    assert len(r.json()["relations"]) > 0


def test_select_respects_limit(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/select", params={"limit": 3})
    assert r.status_code == 200
    assert len(r.json()["edges"]) <= 3
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd ~/larql/crates/larql-python
uv run pytest ~/babel-harness/tests/test_github_lql_bridge.py --collect-only 2>&1 | tail -10
```
Expected: 6 tests collected, no errors at collection time (the fixture will skip or fail at runtime).

- [ ] **Step 3: Write `scripts/github_lql_bridge.py`**

Create `scripts/github_lql_bridge.py`:

```python
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
    pattern = re.compile(r'^INSERT\s+"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"', re.MULTILINE)
    return [(m.group(1), m.group(2), m.group(3), 1.0) for m in pattern.finditer(result.stdout)]


def _hit_to_dict(h: object) -> dict:
    return {
        "layer": h.layer,
        "feature": h.feature,
        "gate_score": float(h.gate_score),
        "target": h.target or "",
    }


@app.get("/v1/stats")
def stats():
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
def walk(
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
def describe(
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
def relations():
    counts: dict[str, int] = {}
    for _, r, _, _ in _state["triples"]:
        counts[r] = counts.get(r, 0) + 1
    return {"relations": [{"name": r, "count": c}
                          for r, c in sorted(counts.items(), key=lambda x: -x[1])]}


@app.get("/v1/select")
def select(
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
def divergence_log():
    return {"log": _state["divergence_log"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub LQL Bridge")
    parser.add_argument("repo", help="GitHub repo (owner/repo)")
    parser.add_argument("--port", type=int, default=8383)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--base-vindex", required=True, dest="base_vindex")
    parser.add_argument("--seed", default="",
                        help="Seed entity for topological assignment (default: first triple subject)")
    args = parser.parse_args()

    print(f"Fetching triples: github://{args.repo}@{args.ref}...")
    triples = _fetch_triples(args.repo, args.ref)
    rel_types = len({r for _, r, _, _ in triples})
    print(f"  {len(triples)} triples, {rel_types} relation types")

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
```

- [ ] **Step 4: Run integration tests**

```bash
cd ~/larql/crates/larql-python
uv run pytest ~/babel-harness/tests/test_github_lql_bridge.py -v -s
```
Expected: `6 passed` (first run takes 3–5 min; subsequent runs faster if GitHub rate-limited responses are cached by `github_graph.py`).

- [ ] **Step 5: Manually verify USE REMOTE works**

```bash
LARQL="$HOME/larql/target/release/larql"
LARQL_PY="$HOME/larql/crates/larql-python"

# Start bridge (background)
cd "$LARQL_PY"
uv run python ~/babel-harness/scripts/github_lql_bridge.py \
  chrishayuk/larql --port 8383 \
  --base-vindex ~/larql-vindexes/smollm2-360m.vindex &
BRIDGE_PID=$!

# Poll up to 120s
for i in $(seq 1 120); do
  curl -sf http://localhost:8383/v1/stats >/dev/null 2>&1 && break
  sleep 1
done

# LQL query via USE REMOTE
printf 'USE REMOTE "http://localhost:8383";\nWALK "gate_knn" TOP 5;\n' | "$LARQL" lql

# Cleanup
kill $BRIDGE_PID 2>/dev/null || true
```

Expected: LQL output contains lines like:
```
Feature scan for "gate_knn"

  L 8: F412   gate=+7.4  top="larql-ternary"
  L11: F88    gate=+6.1  top="gate_vector"
```

- [ ] **Step 6: Commit**

```bash
cd ~/babel-harness
git add scripts/github_lql_bridge.py tests/test_github_lql_bridge.py
git commit -m "feat: github_lql_bridge.py — FastAPI larql-server bridge with topological/spectral divergence logging"
```

---

### Task 3: Coding-Agent Integration and End-to-End Demo

**Files:**
- Modify: `bin/coding-agent` — add `GITHUB_LQL_BRIDGE_URL` context injection block
- Modify: `tests/test-larql-graft.bash` — add two bridge smoke tests (skip unless `GITHUB_LQL_BRIDGE_URL` set)
- Create: `scripts/demo-github-ffn.sh` — end-to-end demonstration script

**Interfaces:**
- Consumes: `github_lql_bridge.py` running at `GITHUB_LQL_BRIDGE_URL`
- Consumes: `larql lql` binary at `${LARQL_BIN:-$HOME/larql/target/release/larql}`
- Consumes: `bin/coding-agent` existing seam vars `TASK_CONTEXT` and `GITHUB_GRAPH_REPO`
- Produces: `coding-agent` that, when `GITHUB_LQL_BRIDGE_URL=http://...` is set, runs `USE REMOTE + WALK` before the task and prepends results to task context; demo script that measures and prints divergence

- [ ] **Step 1: Read the GITHUB_GRAPH_REPO seam in coding-agent**

```bash
grep -n "GITHUB_GRAPH_REPO\|TASK_CONTEXT\|graph_context" bin/coding-agent | head -25
```

Note the exact line numbers of the GITHUB_GRAPH_REPO block and the `TASK_CONTEXT` variable. The next step inserts after the end of that block.

- [ ] **Step 2: Add GITHUB_LQL_BRIDGE_URL block to coding-agent**

Find the line in `bin/coding-agent` that ends the `GITHUB_GRAPH_REPO` handling block (typically a `fi` closing the `if [[ -n "$GITHUB_GRAPH_REPO" ]]` block). Add this block immediately after it:

```bash
# Graph knowledge via live larql bridge (Theory A/B experimental mode)
if [[ -n "${GITHUB_LQL_BRIDGE_URL:-}" ]]; then
  LARQL_BIN="${LARQL_BIN:-$HOME/larql/target/release/larql}"
  if [[ -x "$LARQL_BIN" ]]; then
    _BRIDGE_CONTEXT=$(printf 'USE REMOTE "%s";\nWALK "%s" TOP 10;\n' \
        "$GITHUB_LQL_BRIDGE_URL" "${TASK:-}" \
      | "$LARQL_BIN" lql 2>/dev/null || true)
    if [[ -n "$_BRIDGE_CONTEXT" ]]; then
      TASK_CONTEXT="${TASK_CONTEXT:-}
## Graph Knowledge (github-as-FFN bridge: $GITHUB_LQL_BRIDGE_URL)
\`\`\`
$_BRIDGE_CONTEXT
\`\`\`
"
    fi
  fi
fi
```

- [ ] **Step 3: Run test suite to verify no regressions**

```bash
bash tests/test-coding-agent.bash 2>&1 | tail -3
```
Expected: `=== Results: 41 passed, 0 failed ===`

- [ ] **Step 4: Add bridge smoke tests to test-larql-graft.bash**

Open `tests/test-larql-graft.bash`. Find the final `echo "=== Results..."` block. Insert before it:

```bash
# ── Tests 5–6: Bridge smoke (only when GITHUB_LQL_BRIDGE_URL set) ─────────────
if [[ -n "${GITHUB_LQL_BRIDGE_URL:-}" ]]; then
  STATS=$(curl -sf "${GITHUB_LQL_BRIDGE_URL}/v1/stats" 2>/dev/null || echo '{}')
  if echo "$STATS" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); assert d.get('model','').startswith('github://')" \
      2>/dev/null; then
    ok "bridge /v1/stats — model starts with github://"
  else
    fail "bridge /v1/stats" "model field missing or wrong: $STATS"
  fi

  WALK=$(curl -sf "${GITHUB_LQL_BRIDGE_URL}/v1/walk?prompt=gate_knn&top=3" 2>/dev/null || echo '{}')
  if echo "$WALK" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); assert 'hits' in d and 'divergence' in d" \
      2>/dev/null; then
    ok "bridge /v1/walk — hits + divergence present"
  else
    fail "bridge /v1/walk" "missing hits or divergence: $WALK"
  fi
else
  echo "  SKIP bridge tests (set GITHUB_LQL_BRIDGE_URL=http://... to enable)"
fi
```

- [ ] **Step 5: Run graft tests with bridge active**

Terminal 1 — start bridge:
```bash
cd ~/larql/crates/larql-python
uv run python ~/babel-harness/scripts/github_lql_bridge.py \
  chrishayuk/larql --port 8383 \
  --base-vindex ~/larql-vindexes/smollm2-360m.vindex
```

Terminal 2 — run tests (after bridge prints "Bridge ready"):
```bash
GITHUB_LQL_BRIDGE_URL=http://localhost:8383 bash tests/test-larql-graft.bash
```
Expected: `=== Results: 6 passed, 0 failed ===` (4 original + 2 bridge)

- [ ] **Step 6: Write the end-to-end demo script**

Create `scripts/demo-github-ffn.sh`:

```bash
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
  printf 'USE REMOTE "%s";\nWALK "gate_knn" TOP 5;\n' "$BRIDGE_URL" | "$LARQL_BIN" lql
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
```

Make it executable:
```bash
chmod +x scripts/demo-github-ffn.sh
```

- [ ] **Step 7: Run the full end-to-end demo**

```bash
bash scripts/demo-github-ffn.sh 2>&1 | tee /tmp/demo-out.txt
```

Expected output structure (exact Jaccard values will vary):
```
=== GitHub-as-FFN Bridge Demo ===
  repo: chrishayuk/larql
  base vindex: smollm2-360m.vindex

Starting bridge...
..........ready.
  847 triples across 32 layers

=== WalkFfn Queries ===
  WALK "gate_knn" → [larql-ternary, gate_vector, walk_result]  Jaccard=0.667
  WALK "entity_walk" → [larql-vindex, walk_hit, layer_band]    Jaccard=0.500
  WALK "vindex insert" → [larql-vindex, gate_knn]              Jaccard=0.333
  WALK "Laplacian eigenvector" → [spectral_gap, graph_lapl]    Jaccard=0.400

=== LQL Session via USE REMOTE ===
Feature scan for "gate_knn"

  L 8: F412   gate=+7.4  top="larql-ternary"
  L11: F88    gate=+6.1  top="gate_vector"
  ...

=== Coding Agent Task ===
[agent output using graph knowledge]

=== Theory A vs B Divergence Summary ===
  5 queries logged
  Jaccard range: 0.333 – 0.667
  High-divergence queries (Theory A ≠ Theory B):
    "vindex insert"
      topo-only: ['gate_vector']
      spec-only: ['larql-knowledge']

Demo complete.
```

- [ ] **Step 8: Commit**

```bash
cd ~/babel-harness
git add bin/coding-agent tests/test-larql-graft.bash scripts/demo-github-ffn.sh
git commit -m "feat: coding-agent bridge integration + demo-github-ffn.sh end-to-end demo"
```

---

## Self-Review

**1. Spec coverage:**
- Topological assignment `l(v) = floor(d(v,seed)/D × L)`: `compute_topological_assignment`, Task 1 ✓
- Spectral assignment `l(v) = argmax_l Σ|x̂_v(i)|²`: `compute_spectral_assignment`, Task 1 ✓
- Relation-type layer `l(r) = round(mean)`: both functions, Task 1 ✓
- Explicit `layer=` in `vindex.insert()`: `build_graph_vindex`, Task 1 ✓
- No base model required: `build_graph_vindex` needs only the embedding space from a base vindex; no probing via `RelationClassifier` ✓
- larql-server HTTP protocol (`/v1/stats`, `/v1/walk`, `/v1/describe`, `/v1/relations`, `/v1/select`): all implemented in bridge, Task 2 ✓
- `USE REMOTE "http://..."` compatibility: verified live in Task 2 Step 5 ✓
- Divergence logging per query (Jaccard similarity): Task 2, `/v1/divergence-log` endpoint ✓
- Theory A vs Theory B as competing hypotheses, divergence as observable: explicit in both code comments and demo output ✓
- `GITHUB_LQL_BRIDGE_URL` seam in coding-agent: Task 3 Step 2 ✓
- All commits in `metavacua/babel-harness`: each task commits to `~/babel-harness`, read-only GitHub calls only ✓
- Solid demonstration with real dev task: Task 3 Step 7 (coding-agent describes WalkFfn from live graph context) ✓

**2. Placeholder scan:** None. All code blocks are complete. Task 3 Step 1 instructs reading the file first because the exact line number is not knowable without reading — this is a required orientation step, not a placeholder.

**3. Type consistency:**
- `Triple = tuple[str, str, str, float]` defined in `graph_vindex.py`, imported in `github_lql_bridge.py` ✓
- `build_graph_vindex(...) -> tuple[object, dict[str, int], int]` — bridge unpacks as `vindex_topo, _, n = build_graph_vindex(...)` ✓
- `WalkHit` attributes `.layer`, `.feature`, `.gate_score`, `.target` used in `_hit_to_dict` — consistent with larql-python README ✓
- `vindex.num_layers`, `vindex.hidden_size` used in `/v1/stats` — PyO3-exposed properties per vindex.rs ✓
- `entity_walk(entity, layers=layer_list, top_k=top)` keyword args — consistent with larql-python README ✓
- `TASK_CONTEXT` variable name used in Task 3 Step 2 — must match existing var name found in Step 1; if different, use the actual name ✓
