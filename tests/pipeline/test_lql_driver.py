# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests pinned to the REAL canonical LQL output formats, verified against
larql-canonical @ ab7a08c1 (crates/larql-lql). Each fixture cites the exact
source file/format string it reproduces — see comments below.
"""
from __future__ import annotations

import types
from unittest.mock import patch

from scripts.pipeline.lql_driver import (
    CliLqlDriver,
    build_insert_script,
    parse_describe,
    parse_infer,
)


# ─── BEGIN/SAVE PATCH grammar ───
# Source: crates/larql-lql/src/parser/patch.rs
#   parse_begin: BEGIN PATCH <path-string> ";"   (path REQUIRED)
#   parse_save:  SAVE PATCH ";"                  (NO argument)
# Also crates/larql-lql/src/ast.rs ~L159-169:
#   BeginPatch { path: String }, SavePatch (unit variant, no field)
# Also crates/larql-lql/docs/spec.md §2.5 (~L559-576):
#   BEGIN PATCH "medical-knowledge.vlp";
#   ...
#   SAVE PATCH;

def test_insert_script_grammar_exact():
    s = build_insert_script(
        vindex="/v/x.vindex",
        edge={"s": "docs/a.md", "r": "part-of-matter", "o": "matter:coop"},
        layer=26, mode="COMPOSE", alpha=0.12, patch_path="/p/step-1.vlp")
    assert 'USE "/v/x.vindex";' in s
    # path moves from SAVE to BEGIN, and is REQUIRED on BEGIN PATCH.
    assert 'BEGIN PATCH "/p/step-1.vlp";' in s
    assert ('INSERT INTO EDGES (entity, relation, target) '
            'VALUES ("docs/a.md", "part-of-matter", "matter:coop") '
            'AT LAYER 26 ALPHA 0.12 MODE COMPOSE;') in s
    # SAVE PATCH takes NO argument.
    assert 'SAVE PATCH;' in s
    assert 'SAVE PATCH "' not in s


def test_begin_patch_precedes_save_patch_and_insert():
    s = build_insert_script(
        vindex="/v/x.vindex",
        edge={"s": "a", "r": "b", "o": "c"},
        layer=10, mode="KNN", alpha=None, patch_path="/p.vlp")
    begin_idx = s.index('BEGIN PATCH "/p.vlp";')
    insert_idx = s.index("INSERT INTO EDGES")
    save_idx = s.index("SAVE PATCH;")
    assert begin_idx < insert_idx < save_idx


def test_knn_mode_omits_alpha():
    s = build_insert_script(vindex="/v", edge={"s": "a", "r": "b", "o": "c"},
                            layer=20, mode="KNN", alpha=None, patch_path="/p.vlp")
    assert "ALPHA" not in s and "MODE KNN" in s


# ─── INFER parsing ───
# Source: crates/larql-lql/src/executor/query/infer.rs
#   normal row:       format!("  {:2}. {:20} ({:.2}%)", i + 1, tok, prob * 100.0)
#   KNN-override row:  format!("   1. {:20} (100.00%, {})", ovr.token, summary)
#   (summary itself may contain nested parens, e.g. "model_top1=Paris (42.00%)")

def test_parse_infer_lines_basic():
    raw = 'larql> INFER "x" TOP 3;\n  1. Paris (60.5%)\n  2. Lyon (10.2%)\n'
    out = parse_infer(raw)
    assert out[0] == ("Paris", 0.605) and out[1] == ("Lyon", 0.102)


def test_parse_infer_long_token_row():
    # {:20} does not truncate a token longer than 20 chars; the literal
    # template space before "(" is still emitted (infer.rs L62).
    long_tok = "matter:cooperative-investment-law"
    assert len(long_tok) >= 20
    raw = f"Predictions (walk FFN):\n   3. {long_tok} (12.34%)\n  {{:.0}}ms\n"
    out = parse_infer(raw)
    assert (long_tok, 0.1234) in out


def test_parse_infer_knn_override_row_is_captured_as_normal_entry():
    # Source: infer.rs — the KNN-override branch:
    #   out.push(format!("   1. {:20} (100.00%, {})", ovr.token, summary))
    # summary from helpers.rs::format_knn_override_summary, e.g.:
    #   "source=knn_override/post_logits, cos=0.99, L26, model_top1=Paris (42.00%)"
    raw = (
        "Predictions (walk FFN):\n"
        "   1. London               (100.00%, source=knn_override/post_logits, "
        "cos=0.99, L26, model_top1=Paris (42.00%))\n"
        "  2. Colchester          (3.10%)\n"
        "  15ms\n"
        "  note: KNN override is a post-logits retrieval sidecar, not an "
        "FFN/residual edit.\n"
    )
    out = parse_infer(raw)
    assert ("London", 1.0) in out
    assert ("Colchester", 0.031) in out


# ─── DESCRIBE parsing ───
# Source: crates/larql-lql/src/executor/query/describe/exec.rs +
# crates/larql-lql/src/executor/query/describe/format.rs.
# CliLqlDriver.describe() issues bare `DESCRIBE "<entity>";` with no mode
# clause, so DescribeMode::default() == Brief (parser/query.rs default;
# parser/tests.rs L504,L1073: "brief is the default").
# exec.rs: out = [entity, "  signal: ...", "  <Band> (L..-..):", <edge rows>]
# format.rs::format_brief:
#   format!("    {} → {:20} {:>7.1}  L{:<3}", label12, target, gate, layer)
# where label12 is blank (12 spaces) when the edge has no probe label.

def _brief_row(label: str, target: str, gate: float, layer: int) -> str:
    """Byte-exact reproduction of describe/format.rs::format_brief's
    format string, using Rust-equivalent field widths."""
    label12 = f"{label:<12}"
    return f"    {label12} → {target:<20} {gate:>7.1f}  L{str(layer):<3}"


def test_parse_describe_brief_rows_no_label_single_word_target():
    raw = "\n".join([
        "France",
        "  signal: clean (2 edges, max gate 22.0)",
        "  Edges (L14-27):",
        _brief_row("", "Paris", 9.2, 27),
        _brief_row("", "Europe", 14.4, 25),
    ]) + "\n"
    out = parse_describe(raw, "France")
    assert ("France", "", "Paris") in out
    assert ("France", "", "Europe") in out
    assert len(out) == 2


def test_parse_describe_multi_word_target_and_labelled_row():
    # target field boundary must be derived from the trailing
    # "<gate> L<layer>" anchor, not from whitespace-splitting, since a
    # target may itself contain an embedded space (format.rs {:20} does
    # not escape/quote the target string).
    raw = "\n".join([
        "matter:coop",
        "  signal: moderate (2 edges, max gate 15.0)",
        "  Edges (L14-27):",
        _brief_row("capital", "New York", 14.7, 23),
        "  Output (L28-33):",
        _brief_row("", "French Revolution", 35.2, 15),
    ]) + "\n"
    out = parse_describe(raw, "matter:coop")
    assert ("matter:coop", "capital", "New York") in out
    assert ("matter:coop", "", "French Revolution") in out


def test_parse_describe_not_found_returns_empty():
    # exec.rs: Ok(vec![format!("{entity}\n  (not found)")]) — one string
    # containing an embedded newline, no arrow rows at all.
    raw = "matter:nope\n  (not found)\n"
    out = parse_describe(raw, "matter:nope")
    assert out == []


def test_cli_lql_driver_describe_passes_entity_through(tmp_path):
    driver = CliLqlDriver(larql_bin="larql", vindex="/v/x.vindex")
    fake_raw = "France\n  Edges (L14-27):\n" + _brief_row("", "Paris", 9.2, 27) + "\n"
    with patch.object(driver, "_repl", return_value=fake_raw) as m:
        out = driver.describe("France")
    assert m.called
    assert ("France", "", "Paris") in out


# ─── insert_step ok-detection (line-anchored "Error:" prefix) ───
# Source: crates/larql-cli/src/main.rs:603 and
# crates/larql-cli/src/commands/primary/run_cmd.rs:429,514 —
#   eprintln!("Error: {e}");
# stdout/stderr are captured as two SEPARATE buffers by
# subprocess.run(capture_output=True) — not chronologically interleaved —
# so each stream must be scanned independently for a line matching ^Error:.

def _fake_result(stdout: str, stderr: str):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr)


def test_insert_step_ok_true_when_no_error_line():
    fake = _fake_result(
        stdout='larql> BEGIN PATCH "p.vlp";\nOK\nlarql> SAVE PATCH;\nOK\n',
        stderr="",
    )
    driver = CliLqlDriver(larql_bin="larql", vindex="/v/x.vindex")
    with patch("scripts.pipeline.lql_driver.run_serial", return_value=fake):
        result = driver.insert_step(
            edge={"s": "a", "r": "b", "o": "c"}, layer=20, mode="KNN",
            alpha=None, patch_path="/p.vlp")
    assert result.ok is True


def test_insert_step_ok_false_when_error_line_appears_after_warning():
    # A warning line contains the lowercase substring "error" — the OLD
    # substring-after-warning-split heuristic would have masked a real
    # error line that appears later, on a different stream. This case
    # verifies the new line-anchored check still catches it.
    fake = _fake_result(
        stdout=(
            'larql> BEGIN PATCH "p.vlp";\n'
            "warning: potential error in downstream cache, ignoring\n"
            "OK\n"
        ),
        stderr="Error: patch application failed\n",
    )
    driver = CliLqlDriver(larql_bin="larql", vindex="/v/x.vindex")
    with patch("scripts.pipeline.lql_driver.run_serial", return_value=fake):
        result = driver.insert_step(
            edge={"s": "a", "r": "b", "o": "c"}, layer=20, mode="KNN",
            alpha=None, patch_path="/p.vlp")
    assert result.ok is False


def test_insert_step_ok_true_when_error_word_only_embedded_in_warning():
    # The word "error" appears only as a lowercase substring inside a
    # warning line (never as a line-anchored "Error:" prefix on either
    # stream) — this must NOT be treated as failure.
    fake = _fake_result(
        stdout="warning: an error-prone edge was skipped, continuing\nOK\n",
        stderr="",
    )
    driver = CliLqlDriver(larql_bin="larql", vindex="/v/x.vindex")
    with patch("scripts.pipeline.lql_driver.run_serial", return_value=fake):
        result = driver.insert_step(
            edge={"s": "a", "r": "b", "o": "c"}, layer=20, mode="KNN",
            alpha=None, patch_path="/p.vlp")
    assert result.ok is True
