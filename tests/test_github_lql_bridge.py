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
        ["uv", "run", "python", "-u", str(SCRIPTS / "github_lql_bridge.py"),
         "chrishayuk/larql", "--port", str(BRIDGE_PORT),
         "--base-vindex", str(BASE_VINDEX), "--ref", "main"],
        cwd=LARQL_PY_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    import httpx
    for _ in range(420):  # 7 min: full-repo build takes ~310s on this hardware
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
