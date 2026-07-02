# SPDX-License-Identifier: AGPL-3.0-or-later
"""Demo matrix: query paths x {patched, ablation}. Attribution control (N4):
a cell demonstrates VINDEX knowledge only if patched hits AND ablation misses.
Browse and INFER are reported as distinct capabilities (N5) -- never conflated.

Task 14 adaptations to the task-14-brief.md reference code (each traces to a
verified Task 12/13 finding):

1. PATCH LOADING -- there is NO baked "patched vindex" directory. The
   "patched" target is the base vindex plus session-scoped `APPLY PATCH`
   preludes for every certified induction patch (see `patch_prelude`),
   applied in the SAME `run_script` call as the query (findings F2/F3 in
   scripts/pipeline/induct.py: a fresh session that applies no patches can
   never observe one). The ablation twin is the identical base vindex with
   an EMPTY prelude. Chat cells cannot receive a patch prelude over HTTP
   (`larql serve` has no APPLY-PATCH-equivalent request), so both chat
   targets observe the same base-vindex server for now -- see
   `run_chat_cell` and scripts/run_matrix.py's `server_patch_args`.
2. GEN PROBES -- infer cells build their probe with `infer_probe`
   (delegates to `scripts.pipeline.lql_session.canonical_prompt`), never the
   brief's "{entity} {relation}" phrasing (proven structurally
   false-negative live -- see canonical_prompt's docstring).
6. Latency + peak-RSS fields are recorded per cell where obtainable.
   `peak_rss_mb` is always None for browse/infer cells: they run through
   `scripts.pipeline.contained.run_serial`'s `subprocess.run`, which blocks
   until the child exits and is reaped -- by the time Python regains
   control there is no `/proc/<pid>` left to sample. Only a still-running
   process (the chat target's `LarqlServer`) can be sampled.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.pipeline.lql_session import canonical_prompt, q


def infer_probe(entity: str, relation: str) -> str:
    """The infer-cell probe string. Delegates to canonical_prompt (Task 14
    adaptation 2) rather than the brief's "{entity} {relation}" phrasing --
    the stored KNN key is the residual of the canonical form, and an
    off-template probe silently never matches (anomaly-B, verified live)."""
    return canonical_prompt(entity, relation)


def certified_patches(cert_path: str | Path) -> list[str]:
    """`.vlp` patch file paths for every certified (I_n_ok true) step in
    certificates.jsonl, in step order (ascending `n`) -- Task 14 adaptation
    1. Sorted explicitly rather than trusting file order: the induction
    harness (scripts/pipeline/induct.py) writes one line per step as it
    runs and always halts at the first failure in practice, but a step
    sequence's correctness must not depend on that being true forever."""
    certs = []
    for line in Path(cert_path).read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if c.get("I_n_ok"):
            certs.append(c)
    certs.sort(key=lambda c: c["n"])
    return [c["patch"] for c in certs]


def patch_prelude(cert_path: str | Path) -> list[str]:
    """`APPLY PATCH "<path>";` statements for every certified patch, in step
    order -- the session-scoped prelude that constitutes the "patched"
    target for browse/infer cells. Pass this list first to
    `run_driver_cell`'s `prelude` parameter; pass `[]` for the ablation
    twin (same base vindex, no patches applied)."""
    return [f"APPLY PATCH {q(p)};" for p in certified_patches(cert_path)]


def cells_from_certs(cert_path: str | Path, limit: int = 12) -> list[dict]:
    """Question battery: the first `limit` certified (I_n_ok true) edges
    from certificates.jsonl, in file order, stopping at the first
    uncertified step (mirrors the brief's `_cells_from_certs`, per Task 14
    adaptation 7 -- every question is answerable ONLY if insertion worked).
    Returns base cells (id/entity/relation/expected/question); query-path
    dispatch (browse/infer/chat/graph-file) happens in scripts/run_matrix.py.
    The "question" field is a natural-language form for CHAT cells only --
    infer cells build their probe separately via `infer_probe`, never from
    this field (adaptation 2)."""
    cells = []
    for line in Path(cert_path).read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if not c.get("I_n_ok"):
            break
        e = c["edge"]
        cells.append({
            "id": f"edge-{c['n']}",
            "entity": e["s"],
            "relation": e["r"],
            "expected": e["o"],
            "question": f'What is the {e["r"].replace("-", " ")} of {e["s"]}?',
        })
        if len(cells) >= limit:
            break
    return cells


def run_driver_cell(driver, query_path: str, cell: dict,
                    prelude: list[str]) -> dict:
    """Run one browse/infer cell against a CliLqlDriver-like object
    (duck-typed: needs `run_script(statements) -> (raw, latency_s)`).

    `prelude` (APPLY PATCH statements, or `[]`) is issued in the SAME
    `run_script` call as the query -- Task 14 adaptation 1 / findings F2-F3:
    patches are session-scoped, so a query run without the prelude in that
    same session can never see a patch applied in some other session.
    Callers pass `patch_prelude(cert_path)` for the "patched" target and
    `[]` for the "ablation" twin (identical base vindex, no patches).

    The raw multi-statement REPL transcript is used as `actual` verbatim
    (substring-matched by `evaluate_pair`), matching how DESCRIBE/INFER rows
    already carry the target/token text -- no extra parsing is needed just
    to prove presence or absence.
    """
    if query_path == "browse":
        stmt = f'DESCRIBE {q(cell["entity"])};'
    elif query_path == "infer":
        stmt = f'INFER {q(infer_probe(cell["entity"], cell["relation"]))} TOP 5;'
    else:
        raise ValueError(f"run_driver_cell: unsupported query_path {query_path!r}")
    raw, latency = driver.run_script(list(prelude) + [stmt])
    return {"actual": raw, "latency_s": round(latency, 3), "peak_rss_mb": None}


def run_chat_cell(server, cell: dict) -> dict:
    """Run one chat cell against a LarqlServer-like object (duck-typed:
    needs `alive() -> bool` and `chat(prompt, max_tokens=...) -> dict`).

    Liveness-bracketed (N7): `alive()` is read immediately before AND after
    the HTTP call, so a crash triggered by (or during) a single request is
    recorded against that cell rather than silently attributed to whichever
    cell happens to run next."""
    alive_before = server.alive()
    t0 = time.monotonic()
    resp = server.chat(cell["question"], max_tokens=48)
    latency = time.monotonic() - t0
    alive_after = server.alive()
    return {
        "actual": json.dumps(resp),
        "latency_s": round(latency, 3),
        "alive_before": alive_before,
        "alive_after": alive_after,
    }


def peak_rss_mb(pid: int | None) -> int | None:
    """Peak resident set size (VmHWM, MiB) of a still-running process, or
    None if unobtainable. See the module docstring (adaptation 6) for why
    this is always None for browse/infer cells."""
    if pid is None:
        return None
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def evaluate_pair(expected: str, patched: dict, ablation: dict) -> dict:
    """Attribution control (N4): a cell demonstrates VINDEX knowledge only if
    `expected` appears in the patched target's actual output AND does NOT
    appear in the ablation twin's. `attribution` is:
      "vindex"     -- patched hits, ablation misses (the demonstrated case)
      "confounded" -- both hit (the knowledge, or a look-alike, was already
                      reachable without the patch series -- never counted
                      as a pass, since it proves nothing about the patch)
      "absent"     -- patched itself misses (nothing to attribute)
    """
    hit_p = expected in patched["actual"]
    hit_a = expected in ablation["actual"]
    if hit_p and not hit_a:
        attribution = "vindex"
    elif hit_p and hit_a:
        attribution = "confounded"
    else:
        attribution = "absent"
    return {"pass": hit_p and not hit_a, "attribution": attribution,
            "patched_actual": patched["actual"][:400],
            "ablation_actual": ablation["actual"][:400]}


def render_report(results: list[dict]) -> str:
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    lines = [f"# Demo matrix report", "",
             f"**{passed}/{total} cells passed** (pass = patched hits AND ablation misses)",
             "", "| id | query path | pass | attribution | latency (s) |",
             "|----|-----------|------|-------------|-------------|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['query_path']} | {'PASS' if r['pass'] else 'FAIL'} "
                     f"| {r.get('attribution','-')} | {r.get('latency_s','-')} |")
    by_path: dict[str, list] = {}
    for r in results:
        by_path.setdefault(r["query_path"], []).append(r["pass"])
    lines += ["", "## By query path (browse != INFER: distinct capabilities)"]
    for qp, ps in sorted(by_path.items()):
        lines.append(f"- **{qp}**: {sum(ps)}/{len(ps)}")

    chat_rows = [r for r in results if r["query_path"] == "chat"]
    if chat_rows:
        flips = sum(1 for r in chat_rows
                    if r.get("alive_before") is False or r.get("alive_after") is False)
        lines += [
            "",
            "## Chat caveat (Task 14 adaptation 1)",
            "`larql serve` has no request-time patch flag as of this run, so chat",
            "cells query the BASE vindex for BOTH the `patched` and `ablation`",
            "targets -- a chat cell can therefore never show `attribution: vindex`",
            "and any PASS/FAIL here reflects the model's un-patched behaviour only.",
            "`server_patch_args` is accepted and recorded per cell for when a",
            "serve-time patch flag exists; it is not wired into the launch command",
            "yet (out of scope for the files touched by Task 14).",
            f"- liveness flips observed (alive() false before/after a cell): {flips}/{len(chat_rows)}",
        ]
    return "\n".join(lines)
