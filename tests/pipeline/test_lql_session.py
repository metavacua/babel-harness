# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the shared session-batching helpers (Task 13, adaptation A).

Fixtures are REAL raw output captured live against the canonical smollm2
vindex (larql-canonical @ ab7a08c1), copied verbatim from
~/work/artifacts/ at Task 12/13 time:

- fixtures/preflight-canary-batch.log: one repl session issuing the four
  canary INFERs TOP 3. Block 2 ("Water is made of hydrogen and") contains
  the whitespace-only rank-2 token row that breaks count-based chunking
  (preflight finding F4) -- parse_infer recovers only 2 of its 3 rows.
- fixtures/anomalyB-verify.log: APPLY PATCH + canonical-template INFER in
  which the KNN override FIRED (100.00%, cos=0.82, L24) -- the
  systematic-debugging anomaly-B verification run.
- fixtures/preflight-session3-describe.log: APPLY PATCH + DESCRIBE +
  INFER in one session -- a real DESCRIBE block followed by a real INFER
  block (used to pin split_describe_blocks' block-boundary rule).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.pipeline.canary import CANARY_PROMPTS
from scripts.pipeline.lql_driver import CliLqlDriver, parse_describe
from scripts.pipeline.lql_session import (
    CANARY_TOP,
    canonical_prompt,
    chunk_infer_rows,
    extract_ms_lines,
    has_error_line,
    measure_canaries_batch,
    parse_knn_override,
    split_describe_blocks,
    split_infer_blocks,
)

FIXTURES = Path(__file__).parent / "fixtures"
CANARY_RAW = (FIXTURES / "preflight-canary-batch.log").read_text()
OVERRIDE_RAW = (FIXTURES / "anomalyB-verify.log").read_text()
DESCRIBE_RAW = (FIXTURES / "preflight-session3-describe.log").read_text()


# ── canonical_prompt: byte-exact vs larql-canonical tuning.rs:195-198 ──
#   let rel_words = relation.replace(['-', '_'], " ");
#   format!("The {rel_words} of {entity} is")

def test_canonical_prompt_matches_rust_template():
    assert canonical_prompt("France", "capital") == "The capital of France is"


def test_canonical_prompt_normalises_dashes_and_underscores():
    # mirrors tuning.rs test: capital-of / capital_of -> "capital of"
    assert (canonical_prompt("France", "capital-of")
            == "The capital of of France is")
    assert (canonical_prompt("France", "capital_of")
            == "The capital of of France is")


def test_canonical_prompt_real_pipeline_relation():
    assert (canonical_prompt("docs/court-record/matters/x/README.md",
                             "part-of-matter")
            == "The part of matter of docs/court-record/matters/x/README.md is")


# ── split_infer_blocks / chunk_infer_rows on the REAL canary batch ──

def test_split_infer_blocks_real_log_finds_four_blocks():
    assert len(split_infer_blocks(CANARY_RAW)) == 4


def test_chunk_infer_rows_real_log_recovers_f4_row_counts():
    groups = chunk_infer_rows(CANARY_RAW, 4)
    # F4: block 2's rank-2 row is a whitespace-only token that
    # parse_infer's (\S+) drops -- 3 requested rows parse to 2.
    assert [len(g) for g in groups] == [3, 2, 3, 3]


def test_chunk_infer_rows_real_log_top1s_match_frozen_baseline():
    groups = chunk_infer_rows(CANARY_RAW, 4)
    tops = [g[0] for g in groups]
    assert tops[0] == ("Paris", 0.2032)
    assert tops[1] == ("oxygen", 0.9577)
    assert tops[2] == ("four", 0.546)
    assert tops[3] == ("east", 0.5751)


def test_chunk_infer_rows_raises_on_block_count_mismatch():
    with pytest.raises(ValueError):
        chunk_infer_rows(CANARY_RAW, 5)


def test_extract_ms_lines_real_log():
    assert extract_ms_lines(CANARY_RAW) == [12467.0, 6125.0, 3882.0, 1893.0]


# ── the KNN-override row (anomaly-B verification, live) ──

def test_knn_override_block_parses_as_top1_certainty():
    groups = chunk_infer_rows(OVERRIDE_RAW, 1)
    rows = groups[0]
    assert rows[0] == ("END", 1.0)     # 100.00% override row
    assert len(rows) == 5              # TOP 5: override + 4 model rows


def test_parse_knn_override_real_fixture():
    block = split_infer_blocks(OVERRIDE_RAW)[0]
    assert parse_knn_override(block) == "END"


def test_parse_knn_override_recovers_spaced_target_token():
    # infer.rs: format!("   1. {:20} (100.00%, {})", ovr.token, summary) --
    # ovr.token is the FULL stored target string (knn_store.rs target_token),
    # which may contain spaces (e.g. has-title values). parse_infer's (\S+)
    # group cannot match such a row AT ALL (it is silently dropped, same
    # failure class as F4), so override detection must anchor on the
    # "(100.00%, source=knn_override" marker instead.
    raw = (
        "Predictions (walk FFN):\n"
        "   1. Title Alpha          (100.00%, source=knn_override/post_logits, "
        "cos=0.82, L24, model_top1= a (8.43%))\n"
        "   2.  a                   (8.43%)\n"
        "  100ms\n"
    )
    assert parse_knn_override(raw) == "Title Alpha"
    # and parse_infer indeed drops the spaced override row (pinning WHY
    # parse_knn_override exists)
    from scripts.pipeline.lql_driver import parse_infer
    assert ("Title", 1.0) not in parse_infer(raw)
    assert all(p < 0.999 for _t, p in parse_infer(raw))


def test_parse_knn_override_none_when_no_override():
    block = split_infer_blocks(CANARY_RAW)[0]
    assert parse_knn_override(block) is None


def test_has_error_line_real_logs_are_clean():
    assert not has_error_line(CANARY_RAW)
    assert not has_error_line(OVERRIDE_RAW)


def test_has_error_line_detects_line_anchored_error():
    raw = "Applied: /x.vlp (1 operations)\nError: Execution error: patch not found: /y.vlp\n"
    assert has_error_line(raw)
    # substring mention of "error" inside a warning line is NOT an error
    assert not has_error_line("warning: an error-prone edge was skipped\nOK\n")


# ── split_describe_blocks ──

def _brief_row(label: str, target: str, gate: float, layer: int) -> str:
    """Byte-exact describe/format.rs::format_brief reproduction (same helper
    as tests/pipeline/test_lql_driver.py)."""
    label12 = f"{label:<12}"
    return f"    {label12} → {target:<20} {gate:>7.1f}  L{str(layer):<3}"


def test_split_describe_blocks_real_session_log():
    # Real session: APPLY + DESCRIBE "__ProbeSubject__" + INFER. The block
    # must stop at the INFER header so the Inference-trace arrows never
    # leak into describe parsing.
    blocks = split_describe_blocks(DESCRIBE_RAW, ["__ProbeSubject__"])
    assert "__ProbeSubject__" in blocks
    rows = parse_describe(blocks["__ProbeSubject__"], "__ProbeSubject__")
    assert ("__ProbeSubject__", "", "ProbeTargetValue") in rows
    assert "Predictions (walk FFN):" not in blocks["__ProbeSubject__"]


def test_split_describe_blocks_multiple_entities_attribute_rows_correctly():
    raw = "\n".join([
        "Using: /v.vindex (32 layers)",
        "Applied: /p1.vlp (1 operations: 1 inserts, 0 updates, 0 deletes)",
        "docs/a.md",
        "  signal: moderate (1 edges, max gate 10.0)",
        "  Edges (L13-25):",
        _brief_row("", "matter:m1", 10.0, 24),
        "docs/b.md",
        "  signal: moderate (1 edges, max gate 10.0)",
        "  Edges (L13-25):",
        _brief_row("", "matter:m2", 10.0, 24),
        "Predictions (walk FFN):",
        "   1. x                    (10.00%)",
        "  10ms",
    ]) + "\n"
    blocks = split_describe_blocks(raw, ["docs/a.md", "docs/b.md"])
    rows_a = parse_describe(blocks["docs/a.md"], "docs/a.md")
    rows_b = parse_describe(blocks["docs/b.md"], "docs/b.md")
    assert [t for (_e, _l, t) in rows_a] == ["matter:m1"]
    assert [t for (_e, _l, t) in rows_b] == ["matter:m2"]


def test_split_describe_blocks_missing_entity_absent_and_not_found_kept():
    raw = "docs/a.md\n  (no edges found)\nPredictions (walk FFN):\n   1. x (1.00%)\n"
    blocks = split_describe_blocks(raw, ["docs/a.md", "docs/missing.md"])
    assert "docs/missing.md" not in blocks
    assert parse_describe(blocks["docs/a.md"], "docs/a.md") == []


# ── CliLqlDriver.run_script (the one lql_driver.py addition) ──

def test_run_script_prepends_use_and_returns_raw_and_latency():
    drv = CliLqlDriver(larql_bin="larql", vindex="/v/x.vindex")
    with patch.object(drv, "_repl", return_value="OK\n") as m:
        raw, latency = drv.run_script(
            ['APPLY PATCH "/p1.vlp";', 'DESCRIBE "docs/a.md";'])
    script = m.call_args[0][0]
    assert script.startswith('USE "/v/x.vindex";\n')
    assert 'APPLY PATCH "/p1.vlp";' in script
    assert 'DESCRIBE "docs/a.md";' in script
    assert raw == "OK\n"
    assert isinstance(latency, float) and latency >= 0.0


# ── measure_canaries_batch drives run_script with prelude + 4 INFERs ──

class _StubDriver:
    """Returns the REAL canary-batch raw for whatever script is issued."""

    def __init__(self, raw: str):
        self.raw = raw
        self.scripts: list[list[str]] = []

    def run_script(self, statements: list[str]) -> tuple[str, float]:
        self.scripts.append(list(statements))
        return self.raw, 1.23


def test_measure_canaries_batch_parses_real_log_and_issues_prelude():
    drv = _StubDriver(CANARY_RAW)
    out, raw, latency = measure_canaries_batch(
        drv, prelude=['APPLY PATCH "/p1.vlp";'])
    assert raw is CANARY_RAW and latency == 1.23
    stmts = drv.scripts[0]
    assert stmts[0] == 'APPLY PATCH "/p1.vlp";'
    infers = [s for s in stmts if s.startswith("INFER ")]
    assert len(infers) == len(CANARY_PROMPTS)
    assert all(f"TOP {CANARY_TOP};" in s for s in infers)
    assert out["The capital of France is"] == ("Paris", 0.2032)
    assert out["Water is made of hydrogen and"] == ("oxygen", 0.9577)
    assert out["Two plus two equals"] == ("four", 0.546)
    assert out["The sun rises in the"] == ("east", 0.5751)


def test_measure_canaries_batch_no_prelude_defaults_to_pure_infer_session():
    drv = _StubDriver(CANARY_RAW)
    _out, _raw, _lat = measure_canaries_batch(drv)
    assert all(s.startswith("INFER ") for s in drv.scripts[0])
