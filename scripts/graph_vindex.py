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
        rel_layer_zero = {r: 0 for _, r, _, _ in triples}
        return {**{v: 0 for v in nodes}, **rel_layer_zero}

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
