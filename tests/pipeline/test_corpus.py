# SPDX-License-Identifier: AGPL-3.0-or-later
import subprocess
from pathlib import Path
from scripts.pipeline.corpus import build_corpus

def _mkrepo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# Title A\n\n## Sec\nSee [b](b.md).\n")
    (tmp_path / "docs" / "b.md").write_text("# Title B\n")
    (tmp_path / "img.png").write_bytes(b"\x89PNG")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path

def test_every_file_appears_in_coverage(tmp_path):
    repo = _mkrepo(tmp_path)
    res = build_corpus(repo)
    paths = {c["path"] for c in res["coverage"]}
    assert {"docs/a.md", "docs/b.md", "img.png"} <= paths

def test_unsupported_type_skipped_not_silent(tmp_path):
    repo = _mkrepo(tmp_path)
    res = build_corpus(repo)
    png = [c for c in res["coverage"] if c["path"] == "img.png"][0]
    assert png["status"] == "skipped" and png["reason"] == "unsupported-extension"

def test_start_and_end_edges_present(tmp_path):
    repo = _mkrepo(tmp_path)
    edges = build_corpus(repo)["edges"]
    assert any(e["s"] == "START" and e["o"] == "docs" for e in edges)
    assert any(e["r"] == "terminal" and e["o"] == "END" for e in edges)

def test_graph_is_well_formed(tmp_path):
    from scripts.pipeline.dag import check
    repo = _mkrepo(tmp_path)
    res = build_corpus(repo)
    assert check(res["edges"]) == []

def test_zero_edge_extraction_recorded_not_silent(tmp_path):
    """Files that extract zero edges are still visible in coverage."""
    repo = _mkrepo(tmp_path)
    # Create a minimal Markdown file that produces no edges
    # (a pure frontmatter or metadata-only file)
    (repo / "docs" / "empty.md").write_text("")
    subprocess.run(["git", "-C", str(repo), "add", "docs/empty.md"], check=True)
    res = build_corpus(repo)
    empty_entry = [c for c in res["coverage"] if c["path"] == "docs/empty.md"][0]
    assert empty_entry["status"] == "extracted" and empty_entry["edge_count"] == 0
