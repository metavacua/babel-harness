"""
Tests for build_repo_patch.py — Vindexfile Form 1 → vindex.insert() + .vlp roundtrip.

Tests are split into three tiers:
  - Pure-Python (no markers): parse helpers, run always
  - @needs_larql: subprocess roundtrip, skipped if base vindex unavailable
  - @needs_network: --remote tests, skipped if network/github unavailable

Timing (empirically confirmed, warm OS cache with larql-server running):
  - 1 insert via vindex.insert(): < 5 s (no forward pass, no feature-file load)
  - 2 inserts: < 5 s
NOTE: Insert ops are PatchOp::Insert (WALK-visible via overrides_gate).
WALK queries (entity_walk) can see these ops. INFER also sees them via gate_knn scan.
"""
from __future__ import annotations
import base64
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
from build_repo_patch import parse_vindexfile_inserts

# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

LARQL_BIN = pathlib.Path.home() / "larql/target/release/larql"
BASE_VINDEX = pathlib.Path.home() / "larql-vindexes/smollm2-360m.vindex"
LARQL_PYTHON_DIR = pathlib.Path.home() / "larql/crates/larql-python"

_larql_ok = BASE_VINDEX.exists() and LARQL_PYTHON_DIR.exists()
needs_larql = pytest.mark.skipif(
    not _larql_ok,
    reason="base vindex or larql-python dir not available",
)

_network_ok = os.environ.get("SKIP_NETWORK_TESTS", "") not in ("1", "true")
needs_network = pytest.mark.skipif(
    not _network_ok,
    reason="network tests disabled (set SKIP_NETWORK_TESTS=0 to enable)",
)


def _run_builder(vf_text: str, tmp_path: pathlib.Path):
    """Write a Vindexfile, run build_repo_patch.py, return (output_vlp, proc)."""
    vf = tmp_path / "Vindexfile"
    vf.write_text(vf_text)
    out = tmp_path / "out.vlp"
    proc = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).parent.parent / "scripts/build_repo_patch.py"),
            "--vindexfile", str(vf),
            "--base-vindex", str(BASE_VINDEX),
            "--output", str(out),
        ],
        capture_output=True,
        text=True,
    )
    return out, proc


# ---------------------------------------------------------------------------
# Task 1: Pure-Python helpers — parse (no larql required)
# ---------------------------------------------------------------------------

def test_parse_extracts_insert_triples():
    text = textwrap.dedent("""\
        FROM /some/base.vindex
        # comment
        EXPOSE browse
        INSERT "coding-agent", "calls", "_check_larql"
        INSERT "_run_goose_larql", "calls", "_run_goose_call"
    """)
    result = parse_vindexfile_inserts(text)
    assert result == [
        ("coding-agent", "calls", "_check_larql"),
        ("_run_goose_larql", "calls", "_run_goose_call"),
    ]


def test_parse_skips_non_insert_lines():
    text = 'FROM base.vindex\nEXPOSE browse\nINSERT "a", "b", "c"\n'
    result = parse_vindexfile_inserts(text)
    assert len(result) == 1 and result[0] == ("a", "b", "c")


def test_parse_empty_vindexfile_returns_empty():
    assert parse_vindexfile_inserts("FROM base.vindex\n# no inserts\n") == []


# ---------------------------------------------------------------------------
# Task 2: CLI + subprocess + .vlp validation (requires base vindex + larql-python)
# ---------------------------------------------------------------------------

@needs_larql
def test_builder_exits_zero_on_valid_vindexfile(tmp_path):
    vf_text = 'INSERT "gate_knn", "calls", "entity_walk"\n'
    _, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, f"builder failed:\n{proc.stderr}"


@needs_larql
def test_builder_creates_vlp_file(tmp_path):
    vf_text = 'INSERT "gate_knn", "calls", "entity_walk"\n'
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert out.exists(), "output .vlp was not created"


@needs_larql
def test_builder_vlp_is_valid_json(tmp_path):
    vf_text = 'INSERT "gate_knn", "calls", "entity_walk"\n'
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    assert "operations" in data
    assert "version" in data
    assert "created_at" in data


@needs_larql
def test_builder_vlp_has_insert_ops_not_insert_knn(tmp_path):
    """Builder produces PatchOp::Insert (WALK-visible) ops, not insert_knn (INFER-only).

    vindex.insert() writes a gate vector to overrides_gate — readable by entity_walk() (WALK).
    insert_knn writes to knn_store — invisible to WALK, only seen by INFER forward pass.
    The github_lql_bridge.py consumer uses entity_walk() exclusively.
    """
    vf_text = textwrap.dedent("""\
        INSERT "gate_knn", "calls", "entity_walk"
        INSERT "entity_walk", "calls", "vindex"
    """)
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    ops = data.get("operations", [])
    assert len(ops) == 2, f"expected 2 ops, got {len(ops)}"
    assert all(op.get("op") == "insert" for op in ops), (
        f"expected all ops to be 'insert', got: {[o.get('op') for o in ops]}"
    )


@needs_larql
def test_builder_insert_op_has_gate_vector_b64(tmp_path):
    """Each insert op carries a gate_vector_b64 — the WALK-queryable gate vector."""
    vf_text = 'INSERT "gate_knn", "calls", "entity_walk"\n'
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    ops = data.get("operations", [])
    assert len(ops) == 1
    b64 = ops[0].get("gate_vector_b64", "")
    assert b64, "gate_vector_b64 must be non-empty"
    raw = base64.b64decode(b64)
    assert len(raw) % 4 == 0 and len(raw) >= 4, (
        "gate_vector_b64 must decode to a non-empty f32 array"
    )


@needs_larql
def test_builder_insert_op_has_down_meta(tmp_path):
    """Insert ops include down_meta with compact serde keys t/i/c and non-zero token id.

    PatchDownMeta uses #[serde(rename)] in Rust:
      top_token → "t", top_token_id → "i", c_score → "c"
    Without down_meta, APPLY PATCH defaults top_token_id=0 — the same stub defect
    that #242 reports for Vindexfile INSERT.
    """
    vf_text = 'INSERT "gate_knn", "calls", "entity_walk"\n'
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    ops = data.get("operations", [])
    assert len(ops) == 1
    op = ops[0]
    down_meta = op.get("down_meta")
    assert down_meta is not None, "insert op must carry down_meta"
    assert down_meta.get("t") == "entity_walk", (
        f"down_meta['t'] (top_token) should be 'entity_walk', got {down_meta.get('t')!r}"
    )
    assert isinstance(down_meta.get("i"), int), "down_meta['i'] (top_token_id) must be int"
    assert down_meta["i"] != 0, "down_meta['i']=0 is the stub defect from #242"


@needs_larql
def test_builder_vlp_walk_roundtrip(tmp_path):
    """Full roundtrip: build .vlp → APPLY PATCH → WALK → inserted target appears.

    This is the functional test that structural checks cannot replace.
    - APPLY PATCH verifies the .vlp JSON schema matches Rust PatchDownMeta
      (compact serde field names t/i/c — not top_token/top_token_id/c_score)
    - WALK "gate_knn" scans overrides_gate (not knn_store), so this confirms
      WALK-visibility of the insert — the property the bridge consumer depends on
    - We check for "entity_walk" in TOP 50 results (not just TOP 5) to avoid
      false negatives; rank position matters less than presence
    """
    vf_text = 'INSERT "gate_knn", "calls", "entity_walk"\n'
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, f"builder failed:\n{proc.stderr}"

    lql = (
        f'USE "{BASE_VINDEX}"; '
        f'APPLY PATCH "{out}"; '
        f'WALK "gate_knn" TOP 50;'
    )
    result = subprocess.run(
        [str(LARQL_BIN), "lql", lql],
        capture_output=True,
        text=True,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"larql lql failed:\n{combined}"
    assert "entity_walk" in combined, (
        f"'entity_walk' not found in WALK TOP 50 output after APPLY PATCH.\n"
        f"This means the insert is not WALK-visible or the .vlp schema is wrong.\n"
        f"Output:\n{combined[:2000]}"
    )


@needs_larql
def test_builder_multi_insert_distinct_slots(tmp_path):
    """Two inserts must land in distinct (layer, feature) slots — not overwrite each other.

    Regression test for the VectorIndex.find_free_feature() mmap/heap split bug:
    the base index reads free slots from on-disk mmap, which is never updated by
    set_gate_vector/set_feature_meta (those go to the heap). So every call to
    find_free_feature() returns the same slot — each new insert silently evicts
    the previous one. PatchedVindex.find_free_feature() tracks the overlay to
    avoid this; the Python binding calls the base version.

    Fix: build_repo_patch.py pre-scans free slots (before any writes, when heap
    is empty and mmap is ground truth), then consumes them in order.
    See larql-vindex/src/patch/overlay.rs:338 for the authoritative description.
    """
    vf_text = textwrap.dedent("""\
        INSERT "alpha_zzz", "calls", "entity_alpha"
        INSERT "beta_zzz", "calls", "entity_beta"
    """)
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, f"builder failed:\n{proc.stderr}"

    data = json.loads(out.read_text())
    ops = data["operations"]
    assert len(ops) == 2

    slots = {(o["layer"], o["feature"]) for o in ops}
    assert len(slots) == 2, (
        f"Both inserts landed in the same slot — slot collision bug.\n"
        f"Slot used: {slots}. Only the last one survives APPLY PATCH.\n"
        f"See VectorIndex.find_free_feature() mmap/heap split (overlay.rs:338)."
    )


@needs_larql
def test_builder_multi_insert_functional_walk_roundtrip(tmp_path):
    """Multi-insert: APPLY PATCH + WALK finds BOTH targets — slot collision would hide the first.

    Functional companion to test_builder_multi_insert_distinct_slots (which checks JSON only).
    If both inserts land in the same slot, only the last target survives APPLY PATCH;
    the first target is permanently unreachable by WALK regardless of the gate vector written.

    Uses two semantically distant entities to minimise cross-retrieval:
      gate_knn → entity_walk   (LARQL gate-KNN domain)
      pi-harness → OPENROUTER_CHECK_URL  (toolchain / env-var domain)
    Both targets must appear in combined WALK output after a single APPLY PATCH.
    """
    vf_text = textwrap.dedent("""\
        INSERT "gate_knn", "calls", "entity_walk"
        INSERT "pi-harness", "calls", "OPENROUTER_CHECK_URL"
    """)
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, f"builder failed:\n{proc.stderr}"

    lql = (
        f'USE "{BASE_VINDEX}"; '
        f'APPLY PATCH "{out}"; '
        f'WALK "gate_knn" TOP 50; '
        f'WALK "pi-harness" TOP 50;'
    )
    result = subprocess.run(
        [str(LARQL_BIN), "lql", lql],
        capture_output=True,
        text=True,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"larql lql failed:\n{combined}"

    assert "entity_walk" in combined, (
        f"'entity_walk' not in WALK output — first insert lost or not WALK-visible.\n"
        f"If both inserts landed in the same slot, only the last target survives APPLY PATCH.\n"
        f"Output:\n{combined[:2000]}"
    )
    assert "OPENROUTER_CHECK_URL" in combined, (
        f"'OPENROUTER_CHECK_URL' not in WALK output — second insert lost or not WALK-visible.\n"
        f"Output:\n{combined[:2000]}"
    )


@needs_larql
def test_builder_exits_nonzero_on_empty_vindexfile(tmp_path):
    vf_text = "FROM base.vindex\n# no inserts\n"
    _, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode != 0, "builder should fail on Vindexfile with no INSERT directives"


@needs_larql
def test_builder_dry_run_prints_triples_and_does_not_create_file(tmp_path):
    vf = tmp_path / "Vindexfile"
    vf.write_text('INSERT "a", "calls", "b"\n')
    out = tmp_path / "out.vlp"
    proc = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).parent.parent / "scripts/build_repo_patch.py"),
            "--vindexfile", str(vf),
            "--base-vindex", str(BASE_VINDEX),
            "--output", str(out),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert not out.exists(), "dry-run must not create the output file"
    assert '"a"' in proc.stdout, "dry-run should echo triples to stdout"
    assert "INSERT INTO EDGES" not in proc.stdout, "dry-run should not print LQL"


# ---------------------------------------------------------------------------
# Task 3: --remote flag (requires network + github_graph.py)
# ---------------------------------------------------------------------------

@needs_larql
@needs_network
def test_remote_and_vindexfile_mutually_exclusive(tmp_path):
    """--remote and --vindexfile are mutually exclusive arguments."""
    vf = tmp_path / "Vindexfile"
    vf.write_text('INSERT "a", "b", "c"\n')
    proc = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).parent.parent / "scripts/build_repo_patch.py"),
            "--remote", "chrishayuk/larql@4a120baf",
            "--vindexfile", str(vf),
            "--base-vindex", str(BASE_VINDEX),
            "--output", str(tmp_path / "out.vlp"),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, "--remote and --vindexfile should conflict"
