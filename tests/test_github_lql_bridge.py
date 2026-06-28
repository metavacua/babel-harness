"""Integration tests for github_lql_bridge.py.

Requires smollm2-360m.vindex. Skips automatically if unavailable.
The fixture uses a synthetic triples file (--triples-file) to avoid GitHub API
rate limiting; bridge startup is ~45s (15s load + 30 inserts * 0.35s * 2).
"""
import pathlib, subprocess, sys, time, textwrap
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
from github_lql_bridge import _select_subgraph

# Synthetic triples in LQL INSERT format — avoids real GitHub API calls.
# Entities match what the integration tests query ("gate_knn", "entity_walk").
_SYNTHETIC_LQL = textwrap.dedent("""\
    INSERT "gate_knn", "calls", "entity_walk"
    INSERT "gate_knn", "calls", "vindex"
    INSERT "gate_knn", "imports", "larql_core"
    INSERT "entity_walk", "calls", "vindex"
    INSERT "entity_walk", "calls", "gate_knn"
    INSERT "entity_walk", "imports", "walk_function"
    INSERT "vindex", "contains", "gate_vectors"
    INSERT "vindex", "contains", "down_features"
    INSERT "vindex", "calls", "entity_walk"
    INSERT "larql_core", "imports", "vindex"
    INSERT "larql_core", "imports", "gate_knn"
    INSERT "walk_function", "calls", "gate_knn"
    INSERT "walk_function", "calls", "entity_walk"
    INSERT "gate_vectors", "contains", "gate_knn"
    INSERT "gate_vectors", "calls", "vindex"
    INSERT "down_features", "calls", "vindex"
    INSERT "down_features", "imports", "larql_core"
    INSERT "larql-vindex", "contains", "gate_vectors"
    INSERT "larql-vindex", "contains", "down_features"
    INSERT "larql-vindex", "calls", "entity_walk"
""")


def test_select_subgraph_limits_by_degree():
    triples = [
        ("hub", "calls", "a", 1.0),
        ("hub", "calls", "b", 1.0),
        ("hub", "calls", "c", 1.0),
        ("leaf_a", "imports", "a", 1.0),
    ]
    result = _select_subgraph(triples, max_nodes=1)
    assert len(result) == 1, "max_nodes=1 should return exactly 1 triple"
    assert result[0][0] == "hub", "the single triple should belong to the highest-degree subject"


def test_select_subgraph_max_nodes_zero_returns_all():
    triples = [("a", "r", "b", 1.0), ("c", "r", "d", 1.0)]
    assert _select_subgraph(triples, max_nodes=0) == triples


def test_select_subgraph_max_nodes_exceeds_entities_returns_all():
    triples = [("a", "r", "b", 1.0)]
    assert _select_subgraph(triples, max_nodes=999) == triples


REPO = pathlib.Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
LARQL_PY_DIR = pathlib.Path.home() / "larql/crates/larql-python"
BASE_VINDEX = pathlib.Path.home() / "larql-vindexes/smollm2-360m.vindex"
BRIDGE_PORT = 18383


@pytest.fixture(scope="module")
def bridge_url(tmp_path_factory):
    if not BASE_VINDEX.exists():
        pytest.skip("smollm2-360m.vindex not available")

    triples_file = tmp_path_factory.mktemp("bridge") / "triples.lql"
    triples_file.write_text(_SYNTHETIC_LQL)

    proc = subprocess.Popen(
        ["uv", "run", "python", "-u", str(SCRIPTS / "github_lql_bridge.py"),
         "test/synthetic", "--port", str(BRIDGE_PORT),
         "--base-vindex", str(BASE_VINDEX),
         "--triples-file", str(triples_file)],
        cwd=LARQL_PY_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    import httpx
    for _ in range(120):  # 2 min: synthetic triples build is ~45s
        try:
            r = httpx.get(f"http://localhost:{BRIDGE_PORT}/v1/stats", timeout=5)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=10)
        pytest.fail(f"bridge did not start within 120s\n"
                    f"stdout: {out.decode()[-500:]}\nstderr: {err.decode()[-500:]}")

    yield f"http://localhost:{BRIDGE_PORT}"
    proc.terminate()
    proc.wait(timeout=10)


_TIMEOUT = 30  # entity_walk cold calls take ~3s each; /v1/walk calls two → ~7s


def test_stats_model_is_github(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/stats", timeout=_TIMEOUT)
    assert r.status_code == 200
    d = r.json()
    assert d["model"].startswith("github://")
    assert d["layers"] > 0
    assert d["features"] > 0


def test_walk_returns_hits_and_divergence(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/walk", params={"prompt": "gate_knn", "top": 5},
                  timeout=_TIMEOUT)
    assert r.status_code == 200
    d = r.json()
    assert "hits" in d and isinstance(d["hits"], list)
    assert "divergence" in d
    assert 0.0 <= d["divergence"]["jaccard"] <= 1.0


def test_walk_divergence_log_accumulates(bridge_url):
    import httpx
    r0 = httpx.get(f"{bridge_url}/v1/divergence-log", timeout=_TIMEOUT)
    count_before = len(r0.json()["log"])
    httpx.get(f"{bridge_url}/v1/walk", params={"prompt": "entity_walk", "top": 3},
              timeout=_TIMEOUT)
    r = httpx.get(f"{bridge_url}/v1/divergence-log", timeout=_TIMEOUT)
    assert r.status_code == 200
    log = r.json()["log"]
    assert len(log) == count_before + 1, "one new entry should be appended per walk call"
    assert "jaccard" in log[-1]


def test_describe_returns_edges(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/describe", params={"entity": "larql-vindex"},
                  timeout=_TIMEOUT)
    assert r.status_code == 200
    assert "edges" in r.json()


def test_relations_nonempty(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/relations", timeout=_TIMEOUT)
    assert r.status_code == 200
    assert len(r.json()["relations"]) > 0


def test_select_respects_limit(bridge_url):
    import httpx
    r = httpx.get(f"{bridge_url}/v1/select", params={"limit": 3}, timeout=_TIMEOUT)
    assert r.status_code == 200
    assert len(r.json()["edges"]) <= 3
