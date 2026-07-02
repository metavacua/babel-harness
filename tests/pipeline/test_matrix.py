# SPDX-License-Identifier: AGPL-3.0-or-later
"""Task 14: demo matrix runner with ablation twin. Build + unit-test with
mocks ONLY -- no larql binary invocations, no servers (a live induction run
is in progress on this machine; see induct.py / run_induction.py)."""
from __future__ import annotations

import json

from scripts.pipeline.matrix import (
    cells_from_certs,
    certified_patches,
    evaluate_pair,
    infer_probe,
    patch_prelude,
    peak_rss_mb,
    render_report,
    run_chat_cell,
    run_driver_cell,
)

# ─── evaluate_pair (brief's 3 tests + confounded/absent edges) ───

def test_pair_passes_when_patched_hits_and_ablation_misses():
    patched = {"actual": "f1.md part-of-matter matter:m1", "latency_s": 1.0}
    ablation = {"actual": "(no matching edges)", "latency_s": 1.0}
    res = evaluate_pair("matter:m1", patched, ablation)
    assert res["pass"] and res["attribution"] == "vindex"


def test_pair_fails_when_ablation_also_hits():
    patched = {"actual": "matter:m1", "latency_s": 1.0}
    ablation = {"actual": "matter:m1 from prior", "latency_s": 1.0}
    res = evaluate_pair("matter:m1", patched, ablation)
    assert not res["pass"] and res["attribution"] == "confounded"


def test_report_renders_pass_fail_counts():
    md = render_report([{"id": "c1", "pass": True, "query_path": "browse",
                         "latency_s": 1.0, "attribution": "vindex"},
                        {"id": "c2", "pass": False, "query_path": "infer",
                         "latency_s": 2.0, "attribution": "confounded"}])
    assert "1/2" in md and "browse" in md and "confounded" in md


def test_pair_confounded_when_both_hit_explicit_edge():
    # both patched and ablation surface the target -- the patch series
    # proves nothing here, so this must never be counted as a pass.
    patched = {"actual": "matter:coop is present", "latency_s": 0.5}
    ablation = {"actual": "matter:coop is present too", "latency_s": 0.5}
    res = evaluate_pair("matter:coop", patched, ablation)
    assert not res["pass"]
    assert res["attribution"] == "confounded"


def test_pair_absent_when_patched_itself_misses():
    # patched misses -- nothing to attribute, regardless of ablation.
    patched = {"actual": "(no matching edges)", "latency_s": 0.5}
    ablation = {"actual": "(no matching edges)", "latency_s": 0.5}
    res = evaluate_pair("matter:coop", patched, ablation)
    assert not res["pass"]
    assert res["attribution"] == "absent"


def test_pair_absent_when_only_ablation_hits():
    # pathological: ablation hits but patched (superset) misses -- still
    # "absent" (defined purely by whether patched hits), not "vindex".
    patched = {"actual": "(no matching edges)", "latency_s": 0.5}
    ablation = {"actual": "matter:coop leaked somehow", "latency_s": 0.5}
    res = evaluate_pair("matter:coop", patched, ablation)
    assert not res["pass"]
    assert res["attribution"] == "absent"


# ─── canonical-prompt usage (adaptation 2) ───

def test_infer_probe_uses_canonical_template_not_brief_phrasing():
    s = infer_probe("docs/court-record/matters/x/README.md", "part-of-matter")
    assert "The " in s and " of " in s and " is" in s
    # the brief's rejected phrasing ("{entity} {relation}") never appears
    assert "part-of-matter" not in s
    assert s == "The part of matter of docs/court-record/matters/x/README.md is"


# ─── cells-from-certs against a realistic fixture (real schema, read-only
# reference: ~/work/artifacts/induction/certificates.jsonl) ───

def _cert_line(n: int, ok: bool, s: str, r: str, o: str) -> str:
    # Realistic shape (fields observed in the live certificates.jsonl;
    # only a subset matters to cells_from_certs/certified_patches, but the
    # fixture carries the full real shape so a schema drift would be
    # visible here rather than only live).
    return json.dumps({
        "n": n,
        "edge": {"s": s, "r": r, "o": o},
        "insert_layer": 24, "lambda_layer": 17, "mode": "KNN",
        "patch": f"/home/metavacua/work/artifacts/induction/step-{n:04d}.vlp",
        "checks": {"insert_ok": ok, "browse_all": ok, "no_collision": ok,
                   "gen_new": ok, "gen_prior": ok, "conserved": ok},
        "checked_gen": [n], "gen_checks": [], "browse_missing": [],
        "canary_after": {"The capital of France is": ["Paris", 0.2032]},
        "canary_flap": None, "error": None, "I_n_ok": ok,
        "latency_s": {"insert": 10.0, "verify": 90.0},
        "tool_ms": {"verify": [1.0]}, "elapsed_s": 100.0,
    })


def _write_fixture_certs(tmp_path, rows):
    p = tmp_path / "certificates.jsonl"
    p.write_text("\n".join(rows) + "\n")
    return p


def test_cells_from_certs_stops_at_first_uncertified_step(tmp_path):
    rows = [
        _cert_line(1, True, "docs/a.md", "part-of-matter", "matter:m1"),
        _cert_line(2, True, "docs/b.md", "part-of-matter", "matter:m1"),
        _cert_line(3, False, "docs/c.md", "part-of-matter", "matter:m1"),
        _cert_line(4, True, "docs/d.md", "part-of-matter", "matter:m1"),
    ]
    cert_path = _write_fixture_certs(tmp_path, rows)
    cells = cells_from_certs(cert_path)
    assert [c["id"] for c in cells] == ["edge-1", "edge-2"]
    assert cells[0]["entity"] == "docs/a.md"
    assert cells[0]["relation"] == "part-of-matter"
    assert cells[0]["expected"] == "matter:m1"
    assert "part of matter" in cells[0]["question"]


def test_cells_from_certs_respects_limit(tmp_path):
    rows = [_cert_line(n, True, f"docs/{n}.md", "part-of-matter", "matter:m1")
            for n in range(1, 6)]
    cert_path = _write_fixture_certs(tmp_path, rows)
    cells = cells_from_certs(cert_path, limit=3)
    assert len(cells) == 3
    assert [c["id"] for c in cells] == ["edge-1", "edge-2", "edge-3"]


# ─── patch-prelude construction ordering ───

def test_certified_patches_filters_uncertified_and_sorts_by_n(tmp_path):
    # deliberately out of file order, with an uncertified step interleaved,
    # to prove ordering comes from sorting by n, not file position.
    rows = [
        _cert_line(2, True, "docs/b.md", "part-of-matter", "matter:m1"),
        _cert_line(3, False, "docs/c.md", "part-of-matter", "matter:m1"),
        _cert_line(1, True, "docs/a.md", "part-of-matter", "matter:m1"),
    ]
    cert_path = _write_fixture_certs(tmp_path, rows)
    patches = certified_patches(cert_path)
    assert patches == [
        "/home/metavacua/work/artifacts/induction/step-0001.vlp",
        "/home/metavacua/work/artifacts/induction/step-0002.vlp",
    ]


def test_patch_prelude_emits_quoted_apply_statements_in_order(tmp_path):
    rows = [
        _cert_line(1, True, "docs/a.md", "part-of-matter", "matter:m1"),
        _cert_line(2, True, "docs/b.md", "part-of-matter", "matter:m1"),
    ]
    cert_path = _write_fixture_certs(tmp_path, rows)
    prelude = patch_prelude(cert_path)
    assert prelude == [
        'APPLY PATCH "/home/metavacua/work/artifacts/induction/step-0001.vlp";',
        'APPLY PATCH "/home/metavacua/work/artifacts/induction/step-0002.vlp";',
    ]


def test_patch_prelude_empty_when_no_certified_steps(tmp_path):
    rows = [_cert_line(1, False, "docs/a.md", "part-of-matter", "matter:m1")]
    cert_path = _write_fixture_certs(tmp_path, rows)
    assert patch_prelude(cert_path) == []


# ─── run_driver_cell: prelude + query issued in the SAME run_script call
# (F2/F3 -- a session that never applies the patch can never see it) ───

class _FakeDriver:
    def __init__(self, raw: str):
        self._raw = raw
        self.calls: list[list[str]] = []

    def run_script(self, statements):
        self.calls.append(list(statements))
        return self._raw, 0.42


def test_run_driver_cell_browse_sends_prelude_and_query_in_one_session():
    drv = _FakeDriver("docs/a.md\n    -> matter:m1  9.0  L20\n")
    cell = {"entity": "docs/a.md", "relation": "part-of-matter", "expected": "matter:m1"}
    prelude = ['APPLY PATCH "/x/step-0001.vlp";']
    result = run_driver_cell(drv, "browse", cell, prelude)
    assert len(drv.calls) == 1
    (statements,) = drv.calls
    assert statements[0] == 'APPLY PATCH "/x/step-0001.vlp";'
    assert statements[-1] == 'DESCRIBE "docs/a.md";'
    assert result["actual"] == drv._raw
    assert result["latency_s"] == 0.42
    assert result["peak_rss_mb"] is None


def test_run_driver_cell_ablation_sends_empty_prelude():
    drv = _FakeDriver("docs/a.md\n  (not found)\n")
    cell = {"entity": "docs/a.md", "relation": "part-of-matter", "expected": "matter:m1"}
    run_driver_cell(drv, "browse", cell, [])
    (statements,) = drv.calls
    assert statements == ['DESCRIBE "docs/a.md";']


def test_run_driver_cell_infer_uses_canonical_probe_not_brief_phrasing():
    drv = _FakeDriver("Predictions (walk FFN):\n   1. matter:m1 (100.00%, source=knn_override)\n")
    cell = {"entity": "docs/a.md", "relation": "part-of-matter", "expected": "matter:m1"}
    run_driver_cell(drv, "infer", cell, [])
    (statements,) = drv.calls
    query_stmt = statements[-1]
    assert 'INFER "The part of matter of docs/a.md is" TOP 5;' == query_stmt
    # the rejected phrasing must never appear in the issued statement
    assert "docs/a.md part-of-matter" not in query_stmt


def test_run_driver_cell_rejects_unknown_query_path():
    import pytest
    drv = _FakeDriver("")
    cell = {"entity": "e", "relation": "r", "expected": "x"}
    with pytest.raises(ValueError):
        run_driver_cell(drv, "chat", cell, [])


# ─── run_chat_cell: liveness bracketing (N7) ───

class _FakeServer:
    def __init__(self, alive_sequence, resp):
        self._alive_seq = list(alive_sequence)
        self._resp = resp
        self.chat_calls: list[tuple[str, int]] = []

    def alive(self):
        return self._alive_seq.pop(0)

    def chat(self, prompt, max_tokens=32):
        self.chat_calls.append((prompt, max_tokens))
        return self._resp


def test_run_chat_cell_brackets_liveness_before_and_after():
    srv = _FakeServer([True, True], {"choices": [{"message": {"content": "matter:m1"}}]})
    cell = {"question": "What is the part of matter of docs/a.md?"}
    result = run_chat_cell(srv, cell)
    assert result["alive_before"] is True and result["alive_after"] is True
    assert "matter:m1" in result["actual"]
    assert srv.chat_calls == [("What is the part of matter of docs/a.md?", 48)]


def test_run_chat_cell_records_liveness_flip():
    # the server dies mid-request -- alive_before True, alive_after False --
    # this must be visible in the cell's own result, not lost.
    srv = _FakeServer([True, False], {"choices": [{"message": {"content": ""}}]})
    result = run_chat_cell(srv, {"question": "q"})
    assert result["alive_before"] is True
    assert result["alive_after"] is False


# ─── peak_rss_mb ───

def test_peak_rss_mb_none_for_missing_pid():
    assert peak_rss_mb(None) is None


def test_peak_rss_mb_none_for_nonexistent_process():
    assert peak_rss_mb(999999999) is None


def test_peak_rss_mb_reads_real_proc_status_for_self():
    import os
    val = peak_rss_mb(os.getpid())
    assert val is None or (isinstance(val, int) and val >= 0)


# ─── report rendering: chat caveat + attribution table ───

def test_report_includes_chat_caveat_and_liveness_flip_count():
    md = render_report([
        {"id": "e1:chat", "pass": False, "query_path": "chat", "latency_s": 1.0,
         "attribution": "confounded", "alive_before": True, "alive_after": False},
        {"id": "e1:browse", "pass": True, "query_path": "browse", "latency_s": 1.0,
         "attribution": "vindex", "alive_before": None, "alive_after": None},
    ])
    assert "Chat caveat" in md
    assert "1/1" in md  # one liveness flip out of one chat row
