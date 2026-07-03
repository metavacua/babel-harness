#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run finite induction on the real vindex -- Task 13.

Serial, contained via the driver (larql-probe cgroup + flock). Consumes the
Task 12 frozen calibration (~/work/artifacts/calibration.json: KNN /
cli-lql, knowledge band from the vindex's own index.json) and the Task 7
graph. Produces, under <artifacts>/induction/:

  step-NNNN.vlp        one reversible patch per accepted step (a rejected
                       step's file is deleted -- rollback = non-application,
                       canonical bug #252)
  certificates.jsonl   one machine-checked I(n) certificate per step
  mapping_meta.json    LayerMap.to_meta() (λ is metadata-only in KNN mode)
  halt.json            the halt certificate: sequence-complete,
                       invariant-failed: <check>, or time-budget-exhausted
                       -- all three are reportable findings (the halt index
                       IS the demonstrated capacity frontier)

Usage: python3 scripts/run_induction.py --bin $LARQL_BIN --vindex <path> \\
         --graph pipeline-out/graph.larql.json --artifacts ~/work/artifacts \\
         --n-max 64 --budget-s 10800
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline.dag import depths as dag_depths          # noqa: E402
from scripts.pipeline.induct import (                          # noqa: E402
    run_induction, select_sequence, size_mem_mb,
)
from scripts.pipeline.lam import LayerMap                      # noqa: E402
from scripts.pipeline.lql_driver import CliLqlDriver           # noqa: E402


def _mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--vindex", required=True)
    ap.add_argument("--graph", type=Path, required=True)
    ap.add_argument("--artifacts", type=Path, required=True)
    ap.add_argument("--n-max", type=int, default=64)
    ap.add_argument("--budget-s", type=int, default=10800)
    # babel-harness#13: resource-aware containment. --mem-mb 0 = auto-size
    # the session cgroup from live MemAvailable (floor = the proven 2500).
    # Run 1 halted at step 7 on the 900 s default subprocess timeout while
    # the cgroup reclaimed the mmapped weights; the timeout bounds
    # containment, not semantics, so it is configurable and defaults higher.
    ap.add_argument("--mem-mb", type=int, default=0)
    ap.add_argument("--timeout-s", type=int, default=3600)
    args = ap.parse_args()

    mem_mb = args.mem_mb if args.mem_mb > 0 else size_mem_mb(_mem_available_mb())
    cal = json.loads((args.artifacts / "calibration.json").read_text())
    graph = json.loads(args.graph.read_text())
    d = dag_depths(graph["edges"])
    lm = LayerMap(cal["k_lo"], cal["k_hi"], d)
    seq = select_sequence(graph["edges"], d, args.n_max)
    drv = CliLqlDriver(args.bin, args.vindex, mem_mb=mem_mb,
                       timeout=args.timeout_s)
    print(json.dumps({"sequence_len": len(seq), "mode": cal["mode"],
                      "insert_layer": cal["k_hi"] - 1,
                      "mem_mb": mem_mb, "timeout_s": args.timeout_s,
                      "first_edges": seq[:3]}, indent=2), flush=True)
    rep = run_induction(drv, lm, seq, cal, args.artifacts / "induction",
                        budget_s=args.budget_s, depths=d)
    print(json.dumps(rep, indent=2), flush=True)
    return 0 if rep["completed"] > 0 else 3


if __name__ == "__main__":
    sys.exit(main())
