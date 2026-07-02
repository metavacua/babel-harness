# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finite induction: one reversible patch per edge; I(n) machine-checked per step.

I(n) for the KNN/cli-lql calibration (Task 12 frozen):
  (a) insert_ok  -- step n's BEGIN/INSERT/SAVE session raised no error line;
  (b) browse_all -- EVERY inserted edge e_1..e_n is DESCRIBE-retrievable
      (target present among its entity's rows) in a session applying
      patches 1..n;
  (c) no_collision / gen_new / gen_prior -- canonical-template INFER
      ("The {rel words} of {entity} is", byte-exact vs tuning.rs) fires the
      KNN override with the edge's OWN target: required for the new edge,
      sampled (min(8, n-1) most recent priors) for prior edges. An override
      that fires with a WRONG target is a collision event (entity-dominant
      keys, anomaly-B finding) and an unconditional I(n) failure;
  (d) conserved  -- canary conservation within tolerance (the Monty-Hall
      redistribution constraint), measured warm in the verify session; on
      failure re-measured once in a fresh session (France-canary margin is
      0.0068 -- flap detection: BOTH readings must fail to halt).
No step's success is inferred from its predecessors.

Session structure per step n (findings F2/F3 -- patches are session-scoped):
  session 1 (insert):  APPLY p_1..p_{n-1}; BEGIN PATCH p_n; INSERT; SAVE PATCH;
  session 2 (verify):  APPLY p_1..p_n; DESCRIBE each distinct inserted
                       entity; INFER canonical(new) TOP 5; INFER
                       canonical(sampled priors) TOP 5; 4 canary INFERs.

Rollback = NON-APPLICATION (canonical bug #252: REMOVE PATCH can never match
a BEGIN/SAVE-created patch -- exec_save_patch writes description:None while
exec_remove_patch matches description==path -- so REMOVE PATCH is never
issued anywhere in this harness): on I(n) failure the step's .vlp file is
deleted, a halt certificate is written, and restoration is verified with a
FRESH session that applies only patches 1..n-1 and re-measures the canaries
at the tighter RESTORE_TOL.

Layer policy (adaptation E): ALL edges insert AT LAYER k_hi - 1 (the KNN
stratum pools at the calibration default layer; strict-λ governs only the
COMPOSE stratum). λ(e) is still computed and recorded per edge as
certificate METADATA -- None (over-deep quarantine) becomes null metadata
and never skips the edge.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.pipeline.canary import CANARY_PROMPTS, conservation_ok
from scripts.pipeline.lql_session import (
    CANARY_TOP,
    canonical_prompt,
    extract_ms_lines,
    has_error_line,
    measure_canaries_batch,
    parse_knn_override,
    q,
    split_describe_blocks,
    split_infer_blocks,
)
from scripts.pipeline.lql_driver import parse_describe, parse_infer

_PRIORITY = {"part-of-matter": 0, "has-title": 1, "cites": 2, "in-category": 3,
             "references": 4, "contains": 5}

GEN_SAMPLE_MAX = 8       # gen-check the min(8, n-1) most recent priors per step
GEN_TOP = 5              # TOP for canonical-template generation checks
RESTORE_TOL = 0.05       # tighter tolerance for post-rollback restoration


def select_sequence(edges: list[dict], depths: dict, n_max: int) -> list[dict]:
    """Deterministic curated sequence: priority part-of-matter > has-title >
    cites > in-category > references > contains; terminal edges and START
    subjects excluded; tie-break (depth, s, r, o); capped at n_max."""
    ranked = sorted(
        (e for e in edges if e["r"] != "terminal" and e["s"] not in ("START",)),
        key=lambda e: (_PRIORITY.get(e["r"], 8), depths.get(e["s"], 99),
                       e["s"], e["r"], e["o"]))
    return ranked[:n_max]


# ── resource-aware cgroup sizing (babel-harness#13) ──
MEM_FLOOR_MB = 2500      # the Task 12 proven envelope -- never go below
MEM_CAP_MB = 3400        # never take more than this even on a roomy host
MEM_RESERVE_MB = 1200    # leave this much MemAvailable to the rest of the host


def size_mem_mb(mem_available_mb: int,
                floor: int = MEM_FLOOR_MB, cap: int = MEM_CAP_MB,
                reserve: int = MEM_RESERVE_MB) -> int:
    """Size the per-session cgroup from live MemAvailable.

    Motivation (measured live, babel-harness#13): verify sessions peak at
    1.84-2.03 GB RSS and GROW with n; under a flat 2500 MB cgroup the
    kernel reclaims the mmapped weight pages once headroom thins, and the
    next forward pass re-faults them from disk -- single INFERs ballooned
    from 0.3-22 s to 76-590 s, and run 1's step-7 verify session blew the
    900 s subprocess timeout. Purely a containment change: the cgroup
    bounds resources, never semantics."""
    return max(floor, min(cap, mem_available_mb - reserve))


def _gen_record(step_n: int, edge: dict, block: str) -> dict:
    """Judge one canonical-template INFER block against its edge.

    Override detection uses parse_knn_override (marker-anchored), NOT
    parse_infer row parsing: the override row prints the FULL stored target
    string (knn_store.rs::target_token = target.to_string()), and a spaced
    target makes that row invisible to parse_infer's `(\\S+)` group. A fired
    override whose token is neither the target nor its head (defensive
    startswith, in case the binary ever prints a truncated form) is a
    collision -- retrieval integrity broken."""
    ovr_tok = parse_knn_override(block)
    rows = parse_infer(block)
    fired = ovr_tok is not None
    target = edge["o"]
    if fired:
        tok = ovr_tok
        match = bool(tok) and (tok == target or target.startswith(tok))
    else:
        tok = rows[0][0] if rows else ""
        match = False
    return {
        "n": step_n,
        "prompt": canonical_prompt(edge["s"], edge["r"]),
        "target": target,
        "top1": ([tok, 1.0] if fired else list(rows[0]) if rows else ["", 0.0]),
        "override_fired": fired,
        "target_match": fired and match,
        "collision": fired and not match,
        "ok": fired and match,
    }


def run_induction(driver, layer_map, sequence: list[dict], cal: dict,
                  out_dir, budget_s: int, depths: dict | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    insert_layer = int(cal["k_hi"]) - 1
    mode = cal.get("mode", "KNN")
    alpha = cal.get("alpha")
    tol = cal["canary_tolerance"]
    baseline = {k: tuple(v) for k, v in cal["canary_baseline"].items()}

    if layer_map is not None:
        (out_dir / "mapping_meta.json").write_text(
            json.dumps(layer_map.to_meta(), indent=2))

    inserted: list[dict] = []      # {"n", "edge", "patch"}
    t0 = time.time()
    certs = (out_dir / "certificates.jsonl").open("w")

    def emit(cert: dict) -> None:
        certs.write(json.dumps(cert) + "\n")
        certs.flush()

    def finish(report: dict, halt_record: dict) -> dict:
        halt_record["completed"] = report["completed"]
        (out_dir / "halt.json").write_text(json.dumps(halt_record, indent=2))
        certs.close()
        return report

    def canary_prelude(patches: list[str]) -> list[str]:
        return [f"APPLY PATCH {q(p)};" for p in patches]

    for n, edge in enumerate(sequence, start=1):
        elapsed = time.time() - t0
        if elapsed > budget_s:
            return finish(
                {"completed": len(inserted), "halted": True,
                 "reason": "time-budget-exhausted"},
                {"halt_index": n, "reason": "time-budget-exhausted",
                 "failed_check": "budget", "rolled_back": False,
                 "restored": None, "elapsed_s": round(elapsed, 1)})

        lam = layer_map.layer(edge) if layer_map is not None else None
        patch = out_dir / f"step-{n:04d}.vlp"
        prior_patches = [it["patch"] for it in inserted]

        checks: dict[str, bool] = {}
        gen_records: list[dict] = []
        checked_gen: list[int] = []
        browse_missing: list[int] = []
        canary_after = None
        flap = None
        error_note = None
        latency: dict[str, float] = {}
        tool_ms: dict[str, list[float]] = {}

        try:
            # ── session 1: apply priors, insert step n as a new patch ──
            alpha_clause = (f" ALPHA {alpha}"
                            if alpha is not None and mode == "COMPOSE" else "")
            s1 = canary_prelude(prior_patches) + [
                f"BEGIN PATCH {q(str(patch))};",
                f"INSERT INTO EDGES (entity, relation, target) "
                f"VALUES ({q(edge['s'])}, {q(edge['r'])}, {q(edge['o'])}) "
                f"AT LAYER {insert_layer}{alpha_clause} MODE {mode};",
                "SAVE PATCH;",
            ]
            raw1, lat1 = driver.run_script(s1)
            latency["insert"] = round(lat1, 3)
            checks["insert_ok"] = not has_error_line(raw1)

            if checks["insert_ok"]:
                # ── session 2: verify I(n) -- browse ALL, gen sample, canaries ──
                all_edges = inserted + [{"n": n, "edge": edge,
                                         "patch": str(patch)}]
                entities = list(dict.fromkeys(
                    it["edge"]["s"] for it in all_edges))
                prior_sample = inserted[-min(GEN_SAMPLE_MAX, len(inserted)):] \
                    if inserted else []
                gen_items = [all_edges[-1]] + prior_sample
                s2 = canary_prelude([it["patch"] for it in all_edges])
                s2 += [f"DESCRIBE {q(e)};" for e in entities]
                s2 += [f"INFER {q(canonical_prompt(it['edge']['s'], it['edge']['r']))} "
                       f"TOP {GEN_TOP};" for it in gen_items]
                s2 += [f"INFER {q(p)} TOP {CANARY_TOP};"
                       for p, _ in CANARY_PROMPTS]
                raw2, lat2 = driver.run_script(s2)
                latency["verify"] = round(lat2, 3)
                tool_ms["verify"] = extract_ms_lines(raw2)

                n_infers = len(gen_items) + len(CANARY_PROMPTS)
                infer_blocks = split_infer_blocks(raw2)
                if len(infer_blocks) != n_infers:
                    raise ValueError(
                        f"verify session: expected {n_infers} INFER blocks, "
                        f"got {len(infer_blocks)}; raw tail: {raw2[-300:]!r}")
                blocks = split_describe_blocks(raw2, entities)

                # (b) browse: every inserted edge, DESCRIBE-based
                for it in all_edges:
                    rows = parse_describe(blocks.get(it["edge"]["s"], ""),
                                          it["edge"]["s"])
                    if not any(t == it["edge"]["o"] for (_e, _l, t) in rows):
                        browse_missing.append(it["n"])
                checks["browse_all"] = not browse_missing

                # (c) generation: new edge required, sampled priors, collisions
                for it, block in zip(gen_items, infer_blocks[:len(gen_items)]):
                    gen_records.append(_gen_record(it["n"], it["edge"], block))
                checked_gen = [it["n"] for it in gen_items]
                checks["no_collision"] = not any(
                    g["collision"] for g in gen_records)
                checks["gen_new"] = gen_records[0]["ok"]
                checks["gen_prior"] = all(g["ok"] for g in gen_records[1:])

                # (d) canary conservation, warm; flap-mitigated (France
                # margin 0.0068: one nondeterministic flip must not halt)
                canary_groups = [parse_infer(b)
                                 for b in infer_blocks[len(gen_items):]]
                canary_after = {
                    prompt: (preds[0] if preds else ("", 0.0))
                    for (prompt, _x), preds in zip(CANARY_PROMPTS, canary_groups)}
                conserved_warm = conservation_ok(baseline, canary_after, tol)
                if conserved_warm:
                    checks["conserved"] = True
                else:
                    fresh, _raw3, lat3 = measure_canaries_batch(
                        driver,
                        prelude=canary_prelude([it["patch"] for it in all_edges]))
                    latency["canary_remeasure"] = round(lat3, 3)
                    conserved_fresh = conservation_ok(baseline, fresh, tol)
                    flap = {"first": {k: list(v) for k, v in canary_after.items()},
                            "first_ok": False,
                            "second": {k: list(v) for k, v in fresh.items()},
                            "second_ok": conserved_fresh}
                    checks["conserved"] = conserved_fresh
        except Exception as exc:  # noqa: BLE001 -- a live-run parse/driver
            # surprise must produce a certificate + rollback, not a stack
            # trace and a dangling patch file.
            checks["exception"] = False
            error_note = f"{type(exc).__name__}: {exc}"

        failed = [k for k, v in checks.items() if not v]
        cert = {
            "n": n,
            "edge": {k: edge[k] for k in ("s", "r", "o") if k in edge},
            "insert_layer": insert_layer,
            "lambda_layer": lam,
            "mode": mode,
            "patch": str(patch),
            "checks": checks,
            "checked_gen": checked_gen,
            "gen_checks": gen_records,
            "browse_missing": browse_missing,
            "canary_after": ({k: list(v) for k, v in canary_after.items()}
                             if canary_after else None),
            "canary_flap": flap,
            "error": error_note,
            "I_n_ok": not failed,
            "latency_s": latency,
            "tool_ms": tool_ms,
            "elapsed_s": round(time.time() - t0, 1),
        }
        emit(cert)

        if failed:
            # ── rollback = non-application (#252): delete the .vlp, then
            # verify restoration in a FRESH session applying only 1..n-1 ──
            patch.unlink(missing_ok=True)
            restored = None
            restored_canary = None
            try:
                restored_canary, _rr, _rl = measure_canaries_batch(
                    driver, prelude=canary_prelude(prior_patches))
                restored = conservation_ok(baseline, restored_canary,
                                           RESTORE_TOL)
            except Exception as exc:  # noqa: BLE001 -- restoration evidence
                restored = None       # is best-effort during a halt
                error_note = f"restore-measure failed: {type(exc).__name__}: {exc}"
            reason = f"invariant-failed: {failed[0]}"
            return finish(
                {"completed": len(inserted), "halted": True, "reason": reason},
                {"halt_index": n, "reason": reason, "failed_check": failed[0],
                 "failed_checks": failed, "rolled_back": True,
                 "vlp_deleted": not patch.exists(), "restored": restored,
                 "restored_canary": ({k: list(v) for k, v in
                                      restored_canary.items()}
                                     if restored_canary else None),
                 "error": error_note,
                 "elapsed_s": round(time.time() - t0, 1)})

        inserted.append({"n": n, "edge": edge, "patch": str(patch)})

    return finish(
        {"completed": len(inserted), "halted": False,
         "reason": "sequence-complete"},
        {"halt_index": None, "reason": "sequence-complete",
         "failed_check": None, "rolled_back": False, "restored": None,
         "elapsed_s": round(time.time() - t0, 1)})
