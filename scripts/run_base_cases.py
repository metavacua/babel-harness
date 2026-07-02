#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Base cases (finite-induction n=1) + calibration freeze -- Task 12.

Order of operations (brief step order):
 0. GRAMMAR SELF-CHECK: prove the pipeline round-trips on the REAL vindex
    before touching any base case. See grammar_self_check().
 1. Measure base canary outputs (MEASURED, never assumed -- smollm2-360m is
    360M params; whatever it predicts becomes the frozen baseline).
 2. Probe the knowledge band: read it from the vindex's own index.json
    (layer_bands.knowledge), falling back to the brief's num_layers formula
    or the CLI defaults. See probe_knowledge_band().
 3. For each base-case class x mode x driver: insert ONE edge as a patch,
    verify retrieval (DESCRIBE for KNN visibility, INFER for COMPOSE
    generation), verify canary conservation, REMOVE PATCH, verify canary
    restored.
 4. Freeze calibration.json with the winning (mode, driver, alpha,
    thresholds).

── Adaptations to the brief's reference pseudocode (hard-fact #1), grounded
   in a live preflight against the REAL binary before the timed run (raw
   logs: ~/work/artifacts/preflight-session{1..4}.log) ──

F1. CliLqlDriver.describe() returns (entity, label, target) triples.
    `label` is the Brief-mode *probe label* (blank for a plain inserted
    edge) -- it is NOT the relation, and the relation does not appear
    anywhere in Brief-mode DESCRIBE output. The brief's reference
    `(edge["s"], edge["r"], edge["o"]) in desc` is therefore never True.
    Fixed here: browse_hit_check() compares TARGET only.

F2. Every CliLqlDriver call spawns a brand-new `larql repl` subprocess
    (fresh mmap of the base vindex). A patch written by BEGIN/INSERT/SAVE
    PATCH in one process is invisible to DESCRIBE/INFER run in a later,
    separate process unless that process first issues
    `APPLY PATCH "<path>";` -- confirmed live: a bare `USE` in a fresh
    process after SAVE PATCH shows no trace of the inserted edge, while
    USE + APPLY PATCH does. Patches are session-scoped, not
    vindex-file-mutating (the base vindex's own files are untouched; only
    the standalone .vlp carries the edit).

F3. REMOVE PATCH requires the patch to be currently APPLIED in that same
    session -- called cold (fresh process, no prior APPLY) it raises
    `Error: Execution error: patch not found: <path>` even though the .vlp
    file itself is untouched on disk.

Together F2+F3 mean the brief's per-call sequence (`d.insert_step(...)`
then separately `d.describe(...)` / `d.infer(...)` / `d.remove_patch(...)`,
each its own subprocess) can never observe the inserted edge and would
silently report every base case as failed for an INTERFACE reason, not a
model-behaviour reason. This script instead composes each verification
phase as ONE multi-statement LQL script executed as a single session,
issuing `APPLY PATCH` to re-load the already-saved .vlp into that fresh
session. Originally that used CliLqlDriver's private `_repl` via a local
helper (lql_driver.py was out of scope for Task 12); Task 13 promoted it
to the public CliLqlDriver.run_script() and moved the shared parsing
helpers to scripts/pipeline/lql_session.py.

Usage: python3 scripts/run_base_cases.py --bin $LARQL_BIN --vindex <path> --out ~/work/artifacts
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline.canary import CANARY_PROMPTS, conservation_ok        # noqa: E402
from scripts.pipeline.lql_driver import (                                  # noqa: E402
    CliLqlDriver, StepResult, parse_describe,
)
from scripts.pipeline.lql_session import (                                 # noqa: E402
    CANARY_TOP, chunk_infer_rows, extract_ms_lines, measure_canaries_batch,
)
from scripts.pipeline.py_driver import py_bindings_available               # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GRAPH_PATH = REPO_ROOT / "pipeline-out" / "graph.larql.json"

# Base-case edges: LITERAL members of pipeline-out/graph.larql.json,
# verified at run time by _assert_edges_in_graph(). The brief's reference
# BASE_EDGES do not exist verbatim in the real corpus (hard-fact #6):
#   brief single: {"s": "docs/index.md", "r": "has-title", "o": "Index"}
#     -- real graph has {"s": "docs/index.md", "r": "has-title",
#        "o": "legal-theory — Index"}; no real has-title target in this
#        corpus is single-subtoken (the only <=1-word value, "legal-theory",
#        is itself 3 BPE subtokens: ['legal', '-', 'theory']).
#   brief multi: {"s": "docs/cross-cutting/patron-as-client.md",
#                 "r": "part-of-matter", "o": "matter:cooperative-investment-law"}
#     -- the (r, o) pair is real (80 occurrences) but never for THIS subject.
# Substitutes below were selected by measuring real subtoken counts against
# the vindex's own tokenizer.json (byte-level BPE, GPT-2 style, as used by
# SmolLM2) with a minimal stdlib re-implementation of the encode algorithm
# (investigative only, not shipped): "END" -> 1 subtoken (single class);
# "matter:cooperative-investment-law" -> 9 subtokens
# ['matter',':','co','operative','-','invest','ment','-','law'], matching
# the brief's "N8" multi-subtoken target (multi class).
BASE_EDGES = {
    "single": {"s": "LICENSE", "r": "terminal", "o": "END"},
    "multi": {"s": "docs/court-record/matters/cooperative-investment-law/README.md",
              "r": "part-of-matter", "o": "matter:cooperative-investment-law"},
}

RETRIEVAL_THRESHOLD = 0.30   # frozen: min top-k probability for gen_hit to count
CANARY_TOL = 0.15            # frozen: max allowed top-1 probability drop (post-insert)
RESTORE_TOL = 0.05           # tighter tolerance for post-REMOVE restoration

# Line-anchored error detector, deliberately duplicated from lql_driver's
# private _ERROR_LINE (same source: crates/larql-cli eprintln!("Error: {e}")
# is a line-anchored "Error:" prefix) -- lql_driver.py is out of scope for
# this task so its private regex is not imported.
_ERROR_LINE = re.compile(r"^Error:")


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def _assert_edges_in_graph(graph_path: Path, edges: dict) -> None:
    """Guard against corpus drift: BASE_EDGES must be literal members of the
    extracted repo graph (hard-fact #6). This is a design-time-verified
    invariant (see the BASE_EDGES comment); this assertion just keeps it
    honest if the graph is regenerated differently in the future."""
    data = json.loads(graph_path.read_text())
    real = {(e["s"], e["r"], e["o"]) for e in data["edges"]}
    missing = [(cls, e) for cls, e in edges.items()
               if (e["s"], e["r"], e["o"]) not in real]
    if missing:
        raise AssertionError(
            f"BASE_EDGES entries not found in {graph_path}: {missing}")


# run_script / split_infer_blocks / chunk_infer_rows / extract_ms_lines /
# measure_canaries_batch lived here originally (validated live in the Task
# 12 run); they are now shared code -- run_script is the public
# CliLqlDriver.run_script() session primitive, the rest live in
# scripts/pipeline/lql_session.py (Task 13 adaptation A, closing the Task 12
# review's untested-helpers finding).


def browse_hit_check(desc: list[tuple[str, str, str]], target: str) -> bool:
    """desc is (entity, label, target) triples (see F1); relation is not
    observable in Brief-mode DESCRIBE output, so this is necessarily a
    target-only membership test."""
    return any(t == target for (_e, _label, t) in desc)


def gen_hit_check(target: str, preds: list[tuple[str, float]],
                   threshold: float = RETRIEVAL_THRESHOLD) -> bool:
    """A hit iff some predicted token is a genuine, non-empty PREFIX of the
    target string with probability >= threshold. Tightened vs. the brief's
    reference formula (`tok.strip().startswith(...) or tok.strip() in
    edge["o"]`): the dropped `in` branch is an unbounded substring test that
    is True for "" (empty/whitespace token) and for any single character
    that happens to occur inside the target (e.g. "E"/"N"/"D" all match
    o="END"). Wires RETRIEVAL_THRESHOLD into an actual decision instead of
    leaving it a dead field in calibration.json."""
    for tok, p in preds:
        t = tok.strip()
        if t and target.startswith(t) and p >= threshold:
            return True
    return False


_SELF_CHECK_ENTITY = "__task12_grammar_self_check__"
_SELF_CHECK_RELATION = "self-check-probe"
_SELF_CHECK_TARGET = "GrammarSelfCheckOK"


def grammar_self_check(drv: CliLqlDriver, out_dir: Path, insert_layer: int
                        ) -> tuple[bool, dict]:
    """Hard-fact #2: before ANY base case, prove the pipeline round-trips on
    the real vindex. parse_infer/parse_describe both return [] on genuine
    no-results AND on a broken parser/grammar mismatch -- indistinguishable
    from the return value alone, so an empty result here must STOP the run
    rather than silently proceed into base cases that would themselves
    report empty results ambiguously.

    INFER is the primary gate: raw next-token prediction always returns
    >=1 row for ANY prompt if the grammar/parser are correct, independent
    of whether the corpus has curated facts about the entity.

    DESCRIBE was FIRST probed against well-known generic entities
    ("France", "Paris", "Einstein") -- that came back empty for all three
    live (see preflight-describe-candidates.log), even though the pipeline
    is demonstrably fine (INFER above proves it; a separate manual
    preflight -- preflight-session{1,2,3}.log -- proved DESCRIBE parses
    correctly on an entity that was actually inserted). This vindex's
    DESCRIBE index is populated by a weight-walk extraction pass, not a
    generic dictionary; absence of "France" there is not evidence of
    breakage, it's exactly the no-results/broken-parser ambiguity this
    self-check exists to avoid mis-signaling. So the DESCRIBE half instead
    round-trips an entity we KNOW must be describable because we insert it
    ourselves, right now, through the identical BEGIN/INSERT/SAVE/APPLY/
    DESCRIBE/REMOVE PATCH path the base cases use below -- the only
    unambiguous DESCRIBE probe available against this vindex, and a
    stronger self-check besides (it exercises the exact grammar surface
    the run depends on, not just an unrelated read path).
    """
    t0 = time.monotonic()
    infer_preds = drv.infer("The capital of France is", top=5)
    infer_latency = time.monotonic() - t0
    infer_ok = len(infer_preds) >= 1

    patch = str(out_dir / "self-check.vlp")
    insert_raw, insert_latency = drv.run_script([
        f'BEGIN PATCH {_q(patch)};',
        f'INSERT INTO EDGES (entity, relation, target) '
        f'VALUES ({_q(_SELF_CHECK_ENTITY)}, {_q(_SELF_CHECK_RELATION)}, '
        f'{_q(_SELF_CHECK_TARGET)}) AT LAYER {insert_layer} MODE KNN;',
        'SAVE PATCH;',
        f'DESCRIBE {_q(_SELF_CHECK_ENTITY)};',
    ])
    insert_error = any(_ERROR_LINE.match(line) for line in insert_raw.splitlines())
    desc_rows = parse_describe(insert_raw, _SELF_CHECK_ENTITY)
    describe_ok = (not insert_error) and any(
        t == _SELF_CHECK_TARGET for (_e, _l, t) in desc_rows)

    remove_raw, remove_latency = drv.run_script([
        f'APPLY PATCH {_q(patch)};',
        f'REMOVE PATCH {_q(patch)};',
    ])
    remove_ok = not any(_ERROR_LINE.match(line) for line in remove_raw.splitlines())

    record = {
        "infer_prompt": "The capital of France is",
        "infer_preds": [list(p) for p in infer_preds],
        "infer_ok": infer_ok,
        "infer_latency_s": infer_latency,
        "describe_roundtrip_entity": _SELF_CHECK_ENTITY,
        "describe_roundtrip_rows": [list(r) for r in desc_rows],
        "describe_roundtrip_raw_tail": insert_raw[-500:],
        "describe_ok": describe_ok,
        "self_check_patch_removed": remove_ok,
        "insert_latency_s": insert_latency,
        "remove_latency_s": remove_latency,
        "note": "generic-entity DESCRIBE probing (France/Paris/Einstein) was tried "
                "first and found uninformative on this vindex -- all three return "
                "'(no edges found)' even though the pipeline works; see "
                "preflight-describe-candidates.log. Replaced with an insert-then-"
                "describe round trip using a synthetic entity this run creates itself.",
        "ok": infer_ok and describe_ok,
    }
    (out_dir / "grammar-self-check.json").write_text(json.dumps(record, indent=2))
    return record["ok"], record


def probe_knowledge_band(vindex_path: Path, fallback_lo: int, fallback_hi: int
                          ) -> tuple[int, int, dict]:
    """Step 2: probe the knowledge band. Primary source: the vindex's own
    index.json `layer_bands.knowledge`, computed by the extraction
    toolchain from real per-layer activation analysis of THIS model --
    strictly better grounding than re-probing generic entities through
    DESCRIBE's Brief-mode output, which has no public per-layer `AT LAYER`
    control in CliLqlDriver's current interface (the grammar supports
    `DESCRIBE ... AT LAYER n`, but describe() does not expose it). Falls
    back to the brief's num_layers-derived formula, then to the CLI
    defaults, if index.json is missing or malformed."""
    info: dict = {"source": "cli-default", "k_lo": fallback_lo, "k_hi": fallback_hi}
    index_path = vindex_path / "index.json"
    try:
        idx = json.loads(index_path.read_text())
        bands = idx.get("layer_bands", {})
        knowledge = bands.get("knowledge")
        if isinstance(knowledge, list) and len(knowledge) == 2:
            k_lo, k_hi = knowledge
            info = {"source": "index.json:layer_bands.knowledge",
                     "k_lo": k_lo, "k_hi": k_hi, "num_layers": idx.get("num_layers"),
                     "layer_bands": bands}
            return k_lo, k_hi, info
        num_layers = idx.get("num_layers")
        if num_layers:
            k_lo, k_hi = num_layers // 2 - 2, num_layers - 5
            info = {"source": "formula(num_layers)", "k_lo": k_lo, "k_hi": k_hi,
                     "num_layers": num_layers}
            return k_lo, k_hi, info
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return fallback_lo, fallback_hi, info


def _flush(out_dir: Path, results: list[dict], latencies: list[dict]) -> None:
    """Write base-cases.jsonl and latency.json from whatever has been
    accumulated so far. Called both on the success path and from every
    early-exit / error path, so a failure partway through the matrix still
    leaves 'base-cases.jsonl intact' (brief requirement #7) instead of an
    all-or-nothing write at the very end."""
    with (out_dir / "base-cases.jsonl").open("w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")
    if latencies:
        summary = {
            "n_calls": len(latencies),
            "total_s": sum(e["latency_s"] for e in latencies),
            "overall": {
                "min_s": min(e["latency_s"] for e in latencies),
                "max_s": max(e["latency_s"] for e in latencies),
                "mean_s": statistics.fmean(e["latency_s"] for e in latencies),
                "median_s": statistics.median(e["latency_s"] for e in latencies),
            },
        }
        (out_dir / "latency.json").write_text(
            json.dumps({"calls": latencies, "summary": summary}, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--vindex", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--graph", type=Path, default=DEFAULT_GRAPH_PATH)
    ap.add_argument("--k-lo", type=int, default=14)
    ap.add_argument("--k-hi", type=int, default=27)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    _assert_edges_in_graph(args.graph, BASE_EDGES)

    latencies: list[dict] = []

    def record_latency(kind: str, seconds: float, ms_lines: list[float] | None = None) -> None:
        entry = {"kind": kind, "latency_s": seconds}
        if ms_lines:
            entry["tool_reported_ms"] = ms_lines
        latencies.append(entry)

    drv = CliLqlDriver(args.bin, args.vindex)

    # ── Step 2 (moved ahead of Step 0): knowledge band ──
    # grammar_self_check's DESCRIBE round trip needs a real AT LAYER value to
    # insert its throwaway probe edge at, so the knowledge-band probe (which
    # has no live dependency of its own -- it just reads the vindex's
    # index.json) runs first.
    k_lo, k_hi, band_info = probe_knowledge_band(Path(args.vindex), args.k_lo, args.k_hi)
    insert_layer = k_hi - 1

    # ── Step 0: grammar self-check ──
    self_check_ok, self_check_record = grammar_self_check(drv, args.out, insert_layer)
    record_latency("self-check-infer", self_check_record["infer_latency_s"])
    record_latency("self-check-describe-roundtrip", self_check_record["insert_latency_s"])
    record_latency("self-check-remove", self_check_record["remove_latency_s"])

    results: list[dict] = [
        {"stage": "knowledge-band", "k_lo": k_lo, "k_hi": k_hi,
         "insert_layer": insert_layer, "info": band_info},
        {"stage": "grammar-self-check", "ok": self_check_ok, "data": self_check_record},
    ]

    if not self_check_ok:
        print("GRAMMAR SELF-CHECK FAILED -- pipeline broken or parsers stale relative "
              "to the real binary; see grammar-self-check.json. STOPPING before any "
              "base case (hard-fact #2).", file=sys.stderr)
        _flush(args.out, results, latencies)
        return 2

    # ── py-bindings: diagnostic only. Never wired into the mutation matrix. ──
    # PyBindingsDriver.insert_step (scripts/pipeline/py_driver.py) is an
    # unconditional NotImplementedError as committed by Task 11 -- calling
    # it would only ever raise, never produce a real result. Per hard-fact
    # #1, that dead call is dropped rather than wired: the gate below
    # (status file says available:true AND bindings actually import) is
    # evaluated and recorded for the report, but even a True result does
    # NOT add PyBindingsDriver to `drivers`, because insert_step is not
    # implemented regardless of import/status-file state.
    py_ok, py_reason = py_bindings_available()
    py_status_path = Path.home() / "work" / "artifacts" / "py-bindings-status.json"
    py_status = None
    if py_status_path.exists():
        try:
            py_status = json.loads(py_status_path.read_text())
        except json.JSONDecodeError:
            py_status = None
    py_gate_would_participate = bool(py_status and py_status.get("available") is True and py_ok)
    drivers = [drv]
    results.append({
        "stage": "py-bindings", "importable": py_ok, "reason": py_reason,
        "status_file_seen": py_status is not None, "status_file": py_status,
        "gate_would_participate": py_gate_would_participate,
        "actually_participates": False,
        "note": "insert_step is an unconditional NotImplementedError in the committed "
                "py_driver.py; dropped rather than wired regardless of the gate above.",
    })

    # ── Step 1: base canary measurement (MEASURED, not assumed; hard-fact #3) ──
    try:
        base_canary, base_raw, base_latency = measure_canaries_batch(drv)
    except Exception as exc:  # noqa: BLE001 -- unexpected failure before any case ran
        results.append({"stage": "canary-baseline-error",
                         "error": f"{type(exc).__name__}: {exc}"})
        _flush(args.out, results, latencies)
        print(f"UNEXPECTED FAILURE measuring canary baseline: {exc!r}. "
              "See base-cases.jsonl.", file=sys.stderr)
        return 1
    record_latency("canary-baseline-batch", base_latency, extract_ms_lines(base_raw))
    results.append({"stage": "canary-baseline",
                     "data": {k: list(v) for k, v in base_canary.items()},
                     "latency_s": base_latency})

    # ── Step 3: base cases ──
    # Each iteration is isolated in its own try/except: an exception in one
    # (edge_class, mode, driver) cell (e.g. an untested COMPOSE-mode output
    # shape) must not discard results already collected from earlier cells,
    # per brief requirement #7 ("exit 3 with base-cases.jsonl intact").
    for edge_class, edge in BASE_EDGES.items():
        for mode in ("KNN", "COMPOSE"):
            for d in drivers:
                patch = str(args.out / f"bc-{edge_class}-{mode}-{d.name}.vlp")
                alpha = 0.10 if mode == "COMPOSE" else None
                try:
                    t0 = time.monotonic()
                    step: StepResult = d.insert_step(edge, layer=insert_layer, mode=mode,
                                                      alpha=alpha, patch_path=patch)
                    insert_latency = time.monotonic() - t0
                    record_latency(f"bc-{edge_class}-{mode}-insert", insert_latency)

                    gen_prompt = f"{edge['s']} {edge['r'].replace('-', ' ')}"

                    if not step.ok:
                        results.append({
                            "stage": "base-case", "class": edge_class, "mode": mode,
                            "driver": d.name, "insert_ok": False,
                            "browse_hit": False, "gen_hit": False, "conserved": False,
                            "patch_removed": False, "restored": False,
                            "edge_gone_after_remove": False,
                            "insert_latency_s": insert_latency,
                            "raw_tail": step.raw[-400:],
                        })
                        continue

                    # -- post-insert verification: APPLY + DESCRIBE + INFER(gen), one session --
                    verify_raw, verify_latency = d.run_script([
                        f'APPLY PATCH {_q(patch)};',
                        f'DESCRIBE {_q(edge["s"])};',
                        f'INFER {_q(gen_prompt)} TOP 5;',
                    ])
                    record_latency(f"bc-{edge_class}-{mode}-verify", verify_latency,
                                    extract_ms_lines(verify_raw))
                    desc = parse_describe(verify_raw, edge["s"])
                    gen_preds = chunk_infer_rows(verify_raw, 1)[0]
                    browse_hit = browse_hit_check(desc, edge["o"])
                    gen_hit = gen_hit_check(edge["o"], gen_preds)

                    # -- canary-after: APPLY + 4x INFER, one session --
                    after_canary, after_raw, after_latency = measure_canaries_batch(
                        d, prelude=[f'APPLY PATCH {_q(patch)};'])
                    record_latency(f"bc-{edge_class}-{mode}-canary-after", after_latency,
                                    extract_ms_lines(after_raw))
                    conserved = conservation_ok(base_canary, after_canary, CANARY_TOL)

                    # -- remove + edge-gone check + canary-restored, one session --
                    # KNOWN UPSTREAM LIMITATION (measured live: removed=False in
                    # every cell): exec_remove_patch (larql-canonical
                    # crates/larql-lql/src/executor/mod.rs:496-513) locates the
                    # applied patch by `p.description.as_deref() == Some(path)`,
                    # but exec_save_patch (mod.rs:395-399) hardcodes
                    # `description: None` and apply_patch preserves whatever the
                    # file carries -- so a BEGIN/SAVE-created patch can NEVER be
                    # matched by REMOVE PATCH "<path>": the binary answers
                    # `Error: Execution error: patch not found: <path>` even when
                    # the patch IS applied in this very session. The grammar sent
                    # here is correct per parser/patch.rs; the mismatch is inside
                    # the binary. Restoration is still guaranteed by process
                    # lifecycle: every session is a fresh mmap of the base vindex
                    # and only sees a patch it explicitly APPLYs (preflight F2),
                    # and SAVE PATCH never mutates base vindex files. `restored`
                    # below is therefore measured with the patch still applied in
                    # this session -- a conservative reading (conservation must
                    # hold even WITH the patch loaded, at the tighter tolerance).
                    remove_raw, remove_latency = d.run_script([
                        f'APPLY PATCH {_q(patch)};',
                        f'REMOVE PATCH {_q(patch)};',
                        f'DESCRIBE {_q(edge["s"])};',
                    ] + [f'INFER {_q(p)} TOP {CANARY_TOP};' for p, _ in CANARY_PROMPTS])
                    record_latency(f"bc-{edge_class}-{mode}-remove-restore", remove_latency,
                                    extract_ms_lines(remove_raw))
                    removed = not any(_ERROR_LINE.match(line)
                                       for line in remove_raw.splitlines())
                    desc_after_remove = parse_describe(remove_raw, edge["s"])
                    edge_gone = not browse_hit_check(desc_after_remove, edge["o"])
                    restore_groups = chunk_infer_rows(remove_raw, len(CANARY_PROMPTS))
                    restored_canary = {}
                    for (prompt, _expected), preds in zip(CANARY_PROMPTS, restore_groups):
                        restored_canary[prompt] = preds[0] if preds else ("", 0.0)
                    restored = conservation_ok(base_canary, restored_canary, RESTORE_TOL)

                    results.append({
                        "stage": "base-case", "class": edge_class, "mode": mode,
                        "driver": d.name, "insert_ok": step.ok,
                        "browse_hit": browse_hit, "gen_hit": gen_hit,
                        "conserved": conserved, "patch_removed": removed,
                        "restored": restored, "edge_gone_after_remove": edge_gone,
                        "insert_latency_s": insert_latency,
                        "verify_latency_s": verify_latency,
                        "canary_after_latency_s": after_latency,
                        "remove_restore_latency_s": remove_latency,
                        "canary_after": {k: list(v) for k, v in after_canary.items()},
                        "canary_restored": {k: list(v) for k, v in restored_canary.items()},
                        "gen_preds": [list(p) for p in gen_preds],
                        "raw_tail": step.raw[-400:],
                    })
                except Exception as exc:  # noqa: BLE001 -- isolate this cell, keep going
                    results.append({
                        "stage": "base-case-error", "class": edge_class, "mode": mode,
                        "driver": d.name, "error": f"{type(exc).__name__}: {exc}",
                    })
                    _flush(args.out, results, latencies)  # keep artifacts durable mid-run
                    print(f"base case {edge_class}/{mode}/{d.name} raised {exc!r}; "
                          "recorded and continuing.", file=sys.stderr)
                    continue

    _flush(args.out, results, latencies)

    # ── Step 4: pick winner and freeze calibration ──
    cases = [r for r in results if r["stage"] == "base-case"]
    winners = [c for c in cases if c["insert_ok"] and c["browse_hit"] and c["conserved"]]
    if not winners:
        print("NO base case passed -- HALT (finding, not silent failure)", file=sys.stderr)
        return 3
    winners.sort(key=lambda c: (not c["gen_hit"], c["mode"] != "KNN"))
    w = winners[0]
    calibration = {"k_lo": k_lo, "k_hi": k_hi,
                   "retrieval_threshold": RETRIEVAL_THRESHOLD,
                   "canary_tolerance": CANARY_TOL,
                   "mode": w["mode"], "driver": w["driver"],
                   "alpha": 0.10 if w["mode"] == "COMPOSE" else None,
                   "frozen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "canary_baseline": {k: list(v) for k, v in base_canary.items()}}
    (args.out / "calibration.json").write_text(json.dumps(calibration, indent=2))
    print(json.dumps({"winner": {k: w[k] for k in ('class', 'mode', 'driver',
                                                    'browse_hit', 'gen_hit')}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
