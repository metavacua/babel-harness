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
