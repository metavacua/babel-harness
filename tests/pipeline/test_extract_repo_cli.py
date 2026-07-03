# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import subprocess
import sys
from pathlib import Path

def test_cli_writes_graph_coverage_provenance(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "a.md").write_text("# A\n[b](b.md)\n")
    (repo / "docs" / "b.md").write_text("# B\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "scripts/extract_repo.py", str(repo),
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    graph = json.loads((out / "graph.larql.json").read_text())
    assert graph["larql_version"] == "1.0"
    assert all(set(e) == {"s", "r", "o", "c"} for e in graph["edges"])
    cov = json.loads((out / "coverage.json").read_text())
    assert {c["path"] for c in cov["coverage"]} == {"docs/a.md", "docs/b.md"}
    prov_lines = (out / "provenance.jsonl").read_text().splitlines()
    assert len(prov_lines) == len(graph["edges"])

def test_cli_fails_loudly_on_empty_corpus(tmp_path, monkeypatch):
    # empty repo (no files) -> START has nothing; fatal exit 2
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    r = subprocess.run([sys.executable, "scripts/extract_repo.py", str(repo),
                        "--out", str(tmp_path / "o2")], capture_output=True, text=True)
    assert r.returncode == 2

def test_cli_quarantines_reachability_violations_and_continues(tmp_path):
    # A file whose only appearance is as an unresolvable reference target produces
    # not-END-coreachable / not-START-reachable violations. Design rule: quarantine
    # into coverage.json (dag_violations), exit 0. Only cycles and empty corpus are fatal.
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    # index.md links a directory that exists only as a link target (placeholder pattern
    # observed on the real corpus: .gitkeep-only dirs referenced from docs/index.md)
    (repo / "docs" / "index.md").write_text("# Index\n[stub](court-record/stub/)\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "scripts/extract_repo.py", str(repo),
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    cov = json.loads((out / "coverage.json").read_text())
    assert any("court-record/stub" in v or "not-" in v for v in cov["dag_violations"])
