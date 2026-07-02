#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the demo matrix. Serial; one server at a time; liveness-bracketed (N7).

Question battery is generated from the FIRST certified (I_n_ok true)
induction edges (certificates.jsonl) so every question is answerable ONLY
if insertion actually worked (scripts.pipeline.matrix.cells_from_certs).

Task 14 adaptations to the task-14-brief.md reference `run_matrix.py` (each
traces to a verified Task 12/13 finding -- see scripts/pipeline/matrix.py's
module docstring for the full rationale):

1. PATCH LOADING -- there is no baked "patched vindex" directory, so this
   script takes ONE base vindex (`--vindex`), not a `--patched`/`--ablation`
   pair. The "patched" target for browse/infer cells is that same vindex
   plus a session-scoped `APPLY PATCH` prelude built from certificates.jsonl
   (`matrix.patch_prelude`); the ablation twin is the identical vindex with
   an empty prelude. Both are issued through `matrix.run_driver_cell`, which
   puts the prelude and the query in the SAME `CliLqlDriver.run_script`
   call -- findings F2/F3 (scripts/pipeline/induct.py) establish that a
   fresh session which never applies a patch can never observe it.

   Chat cells go through `larql serve`, which has no per-request
   patch-application mechanism (patches cannot be `APPLY`-ed over HTTP), so
   both chat targets read the SAME base-vindex server here. `--server
   -patch-args` is accepted and threaded onto each chat result row so a
   later orchestrated run can wire it into the `serve` invocation IF (and
   only if) `"$BIN" serve --help`, probed live and out of this task's
   scope, turns out to support a patch flag -- see the "Chat caveat"
   section `render_report` emits whenever any chat cells are present.

2. GEN PROBES -- infer cells use `matrix.infer_probe` (canonical_prompt's
   "The {rel words} of {entity} is" template), never "{entity} {relation}".

4. The brief's `for qp in (...): pass` dead-scaffolding loop is deleted,
   not reproduced.

5. Liveness bracketing (N7) applies to chat cells only (`run_chat_cell`
   reads `alive()` before AND after each cell); browse/infer/graph-file
   cells have no comparable "server" concept and record alive_* as None.

6. `peak_rss_mb` is recorded per cell where obtainable: always None for
   browse/infer (their subprocess is reaped by `run_serial` before Python
   regains control) and sampled from `/proc/<pid>/status` for the chat
   server, which is still running when each request completes.

Usage: python3 scripts/run_matrix.py --bin $LARQL_BIN \\
  --vindex ~/work/vindexes/smollm2-360m-canonical.vindex \\
  --graph pipeline-out/graph.larql.json --artifacts ~/work/artifacts

Smoke-test note (mirrors the brief's step 5): running this with the SAME
certificates.jsonl but an artificially emptied prelude (e.g. temporarily
pointing --artifacts at a directory with no certificates.jsonl, so
patch_prelude() is `[]`) makes every knowledge cell come back "absent" for
browse/infer, which is the expected non-attribution control result -- it
does NOT exercise the "confounded" control the original brief's
patched==ablation-file smoke test did, because there is no longer a second
vindex file to point at; the equivalent control here is comparing a run's
attribution counts against a run where `patch_prelude()` is forced to `[]`
for BOTH targets.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline.lql_driver import CliLqlDriver     # noqa: E402
from scripts.pipeline.matrix import (                     # noqa: E402
    cells_from_certs,
    evaluate_pair,
    patch_prelude,
    peak_rss_mb,
    render_report,
    run_chat_cell,
    run_driver_cell,
)
from scripts.pipeline.server import LarqlServer          # noqa: E402


def _query_graph_file(cell: dict, bin_: str, graph: Path) -> dict:
    """Graph-file cells need no server/model: the graph file IS the
    knowledge, so patched-vs-ablation is meaningless here -- these cells
    assert presence only (brief's design, unchanged by Task 14)."""
    t0 = time.time()
    r = subprocess.run([bin_, "describe", cell["entity"], "--graph", str(graph)],
                       capture_output=True, text=True, timeout=120)
    actual = r.stdout + r.stderr
    return {"actual": actual, "latency_s": round(time.time() - t0, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--vindex", required=True,
                    help="base canonical vindex -- there is no separate "
                         "baked patched copy (Task 14 adaptation 1); the "
                         "patched target is this file plus a session APPLY "
                         "PATCH prelude built from certificates.jsonl")
    ap.add_argument("--graph", type=Path, required=True)
    ap.add_argument("--artifacts", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=12,
                    help="question battery size (first N certified edges)")
    ap.add_argument("--server-patch-args", nargs="*", default=None,
                    help="EXPERIMENTAL, not yet wired: extra argv for "
                         "`$BIN serve`, for a future patched chat target. "
                         "Recorded per chat result row; has no effect on "
                         "this run until larql's serve-time patch support "
                         "is confirmed (see the module docstring).")
    args = ap.parse_args()

    out = args.artifacts / "matrix"
    out.mkdir(parents=True, exist_ok=True)
    cert_path = args.artifacts / "induction" / "certificates.jsonl"

    cells = cells_from_certs(cert_path, limit=args.limit)
    prelude = patch_prelude(cert_path)
    results: list[dict] = []

    # graph-file cells: presence-only, no patched/ablation split.
    for cell in cells:
        r = _query_graph_file(cell, args.bin, args.graph)
        results.append({"id": f"{cell['id']}:graph-file", "query_path": "graph-file",
                        "pass": cell["expected"] in r["actual"],
                        "attribution": "graph-file",
                        "alive_before": None, "alive_after": None,
                        "peak_rss_mb": None, **r})

    # browse/infer: one driver, patched (prelude applied) vs ablation
    # (empty prelude) issued in the SAME run_script call per adaptation 1.
    drv = CliLqlDriver(args.bin, args.vindex)
    for qp in ("browse", "infer"):
        for cell in cells:
            patched_r = run_driver_cell(drv, qp, cell, prelude)
            ablation_r = run_driver_cell(drv, qp, cell, [])
            ev = evaluate_pair(cell["expected"], patched_r, ablation_r)
            results.append({"id": f"{cell['id']}:{qp}", "query_path": qp,
                            "latency_s": patched_r["latency_s"],
                            "peak_rss_mb": patched_r["peak_rss_mb"],
                            "alive_before": None, "alive_after": None, **ev})

    # chat: ONE server against the base vindex. Per adaptation 1, patches
    # cannot be applied over HTTP, so patched and ablation observe the
    # identical response -- evaluate_pair(row, row) can only ever yield
    # "confounded" or "absent", which is the documented, expected limit.
    with LarqlServer(args.bin, args.vindex) as srv:
        for cell in cells:
            r = run_chat_cell(srv, cell)
            ev = evaluate_pair(cell["expected"], r, r)
            results.append({
                "id": f"{cell['id']}:chat", "query_path": "chat",
                "server_patch_args": args.server_patch_args,
                "peak_rss_mb": peak_rss_mb(srv.proc.pid if srv.proc else None),
                **r, **ev,
            })

    with (out / "results.jsonl").open("w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")
    (out / "report.md").write_text(render_report(results))
    print(render_report(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
