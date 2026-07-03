# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.dag import depths, check

def _e(s, r, o):
    return {"s": s, "r": r, "o": o, "c": 1.0, "prov": "t:0"}

WELL = [
    _e("START", "contains", "docs"),
    _e("docs", "contains", "docs/a.md"),
    _e("docs/a.md", "contains", "docs/a.md#s1"),
    _e("docs/a.md#s1", "terminal", "END"),
    _e("docs/a.md", "references", "docs/b.md"),   # non-hierarchical, no depth role
    _e("docs", "contains", "docs/b.md"),
    _e("docs/b.md", "terminal", "END"),
]

def test_depths_longest_path_from_start():
    d = depths(WELL)
    assert d["START"] == 0 and d["docs"] == 1
    assert d["docs/a.md"] == 2 and d["docs/a.md#s1"] == 3

def test_well_formed_graph_passes():
    assert check(WELL) == []

def test_unreachable_node_flagged():
    bad = WELL + [_e("orphan", "contains", "orphan/child")]
    assert any("not-START-reachable" in v and "orphan" in v for v in check(bad))

def test_dead_end_flagged():
    bad = [
        _e("START", "contains", "docs"),
        _e("docs", "contains", "docs/dead.md"),   # never reaches END
    ]
    assert any("not-END-coreachable" in v and "docs/dead.md" in v for v in check(bad))

def test_cycle_flagged():
    bad = WELL + [_e("docs/a.md#s1", "contains", "docs")]
    assert any("cycle" in v for v in check(bad))
