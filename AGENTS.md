# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) and other coding
agents working in this repository.

## What this project is

babel-harness is Claude Code acting as full-stack operator of a local AI
coding pipeline. It routes coding tasks through three provider paths and
also runs its own repo→vindex pipeline that turns this repository (and
others) into a queryable LARQL vindex:

1. **OpenRouter free-tier** (default, remote) and **Ollama** (offline
   fallback, local) — via `bin/coding-agent` and `bin/pi-harness`.
2. **larql vindex** — this path is REAL, not a design sketch. `scripts/` +
   `scripts/pipeline/` extract a git repository into a graph
   (`scripts/extract_repo.py`), map it onto model layers, and drive
   `larql repl`/`larql serve` under containment to INSERT, verify, and
   query knowledge inside a compiled model. On `smollm2-360m-canonical`
   this pipeline has **certified an 18-edge finite induction with zero
   collisions** (frontier reached by wall-clock budget, not by exhausting
   the edge sequence — see Architectural invariants) and driven a
   **working goose chat demo** through a custom OpenAI-compat shim
   (`scripts/pipeline/chat_shim.py`) that a live session confirmed returns
   the exact inserted edge end-to-end. See "Known limitations" below for
   exactly what is and is not durably re-runnable today.

The previous README ("larql vindex (future)", "Status: Design phase") was
wrong the moment this pipeline started running against a real vindex; see
the updated README.md for the corrected status.

## Workspace layout

```
bin/                    end-user entry points
  coding-agent            OpenRouter/Ollama/larql-serve router for one-shot
                          goose tasks; reference implementation of the
                          server-lifecycle discipline (see Operational laws)
  pi-harness              Pi-agent harness: OpenRouter primary, Ollama
                          fallback, cgroup-scoped
  demo-coding-agent        timing demo of both coding-agent provider paths

scripts/                 the repo->vindex pipeline
  extract_repo.py          CLI: repo -> graph.larql.json + coverage.json +
                            provenance.jsonl (exit 2 on cycles/empty corpus;
                            reachability violations are quarantined, not fatal)
  run_base_cases.py        n=1 base-case patch construction + calibration
  run_induction.py         CLI: drives scripts/pipeline/induct.py's finite
                            induction end to end against a real vindex
  run_matrix.py            CLI: drives scripts/pipeline/matrix.py's demo
                            matrix (graph-file / browse / infer / chat cells)
  run_goose_demo.sh        self-contained goose demo runner (patched vs.
                            ablation), reusing scripts/run_agent_battery.sh
  run_agent_battery.sh     question battery runner used by run_goose_demo.sh

  pipeline/                 library modules used by the CLIs above
    md_extract.py, docbook_extract.py    per-filetype edge extractors
    path_edges.py                        deterministic path-derived edges
                                          (contains/, part-of-matter, in-category)
    corpus.py                            assembles extractors + path_edges
                                          into one deduplicated edge set +
                                          coverage/quarantine report
    dag.py                               START/END DAG: longest-path depth
                                          over `contains` edges, cycle +
                                          reachability checks
    lam.py                               LayerMap: strict-v2 depth->layer
                                          mapping (see invariants below)
    contained.py                         HARD POLICY containment wrapper +
                                          serial-execution lock (see
                                          Operational laws: Containment)
    lql_driver.py / lql_session.py       cli-lql mutation/query driver:
                                          builds LQL scripts, parses `larql
                                          repl` output (pinned against
                                          larql-canonical source, not spec
                                          prose)
    server.py                            LarqlServer: contained `larql
                                          serve` lifecycle + OpenAI-compat
                                          HTTP client (one server per port)
    chat_shim.py                         OpenAI-compat chat shim: per-request
                                          APPLY-PATCH + INFER through cli-lql,
                                          SSE streaming, goose <info-msg>
                                          canonicalization (see Known
                                          limitations: #253)
    canary.py                            fixed canary prompt set +
                                          conservation check (Monty-Hall
                                          redistribution constraint)
    induct.py                            finite induction: one reversible
                                          patch per edge, I(n) certificate
                                          per step, non-application rollback
    matrix.py                            demo matrix: query path x
                                          {patched, ablation} attribution
    py_driver.py                         PyO3-bindings query driver
                                          (secondary; cli-lql is the
                                          calibrated primary, see Task 12)

tests/
  test-*.bash              bash test suites (mocks-only: tests/mocks/{pi,
                           goose,curl,larql,ollama,sudo,systemctl}; never
                           start a live model or shim)
  pipeline/test_*.py       pytest suite for scripts/pipeline/* (145 tests
                           as of this writing; see Build, test, run)

docs/
  specs/                    design docs (docs/specs/2026-06-23-design.md)
  superpowers/plans, specs  session-development-loop planning artifacts

.superpowers/sdd/          task briefs, reports, and progress.md — the
                           session ledger of record for what has actually
                           run, including anomalies and fixes. Read
                           progress.md before assuming a capability works;
                           it records what was verified live vs. what is
                           implemented-but-unrun.

pipeline-out/               a real extract_repo.py run against this repo
                           (graph.larql.json, coverage.json, provenance.jsonl)
LICENSES/                  full license texts (AGPL-3.0-or-later,
                           Apache-2.0, CC-BY-SA-4.0)
.reuse/dep5                REUSE per-directory license declarations
```

There is no `lib/` or `src/` directory yet. `.reuse/dep5` reserves `src/**`
for AGPL-3.0-or-later as a forward-looking glob; nothing lives there today.

## Operational laws

These exist because every one of them was learned the hard way — by a
session thrashing through repeated self-inflicted process kills and
crashes while running the patched-vindex goose demo (see
`.superpowers/sdd/progress.md`, the entries starting "SYSTEMATIC DEBUGGING
(\"terminal crashed/reset\")"). Follow them; do not rediscover them.

### Server lifecycle: discover-before-launch, explicit PID, never pattern-kill

The canonical pattern is `bin/coding-agent`'s `_check_larql` /
`_start_larql_server` (lines 35–56):

```bash
_check_larql() {
  curl -sf --max-time 3 "http://localhost:${LARQL_PORT}/v1/models" > /dev/null 2>&1
}
_start_larql_server() {
  "$LARQL_BIN" serve "$vindex" --port "$LARQL_PORT" > /dev/null 2>&1 &
  local server_pid=$!
  trap "kill $server_pid 2>/dev/null || true" EXIT
  # ... poll _check_larql up to LARQL_START_TIMEOUT, then `trap - EXIT` on success
}
```

Every server/shim launch in this repo must reproduce this shape: **check
health before launching** (never assume a port is free or already serving
what you think it is), **capture the PID explicitly at `&`** (`server_pid=$!`
/ `SHIM_PID=$!`), install a `trap ... EXIT`/`INT`/`TERM` cleanup, and only
release the trap after a successful health-poll. `scripts/run_goose_demo.sh`
applies the identical discipline to the chat-shim path (`SHIM_PID` capture,
`ss -tlnp` port-busy precheck, health-gate polling `/v1/models`, `cleanup`
trap idempotent on EXIT/INT/TERM) and `scripts/pipeline/server.py`'s
`LarqlServer` class applies it in Python (`self.proc`, explicit
`os.killpg`/SIGTERM-then-SIGKILL, `alive()` health check before `start()`).

**One server per port.** `LarqlServer.start()` raises if something already
answers on the target port rather than silently reusing or colliding with
it ("C4: never two").

**HARD RULE: never kill by a pattern that can match your own command
line.** `kill`/`pkill -f 'chat_shim'` or `-f 'scripts/pipeline/chat'`
matches the shell that is *running* that command — pattern-killing
`chat_shim` from inside a script whose own argv contains `chat_shim`
self-terminates the invoking shell (observed live: repeated exit 144,
misread as "terminal crashed"; root cause was self-inflicted, not
environmental — see progress.md). Use the PID captured at launch, or, if
you must search by name, a bracket-glob that doesn't match the grep
command's own argv: `pgrep -f '[c]hat_shim'` (the bracket makes the
pattern not match itself literally).

### Containment: every model-loading command goes through `larql-probe safe`

This host has no swap; an uncontained model load can OOM-crash the whole
container. HARD POLICY: any command that loads or runs a local model
(`larql repl`/`larql serve`, `.gguf` loads, etc.) MUST run through
`larql-probe safe --mem MB --cpus N -- <command>`. In this repo that
wrapper is `scripts/pipeline/contained.py:contained_cmd`, and
`run_serial`/`LarqlServer` are the only sanctioned call sites — never shell
out to `larql repl`/`larql serve` directly from new code. A PreToolUse hook
enforces the wrapper at the Claude Code layer; do not attempt to bypass it.

Known `larql-probe` bugs, tracked upstream on **larql-to-sparql#246**: a
command-substitution pipe-inheritance deadlock (`cmd_safe` could silently
never run the wrapped command at all — fixed locally, see
`~/.local/bin/larql-probe` vs. its `.bak-deadlock`), a root-uid issue, and
a fast-fail arithmetic bug. All three were hit and fixed/mitigated locally
during this pipeline's development; treat the upstream fix as pending and
keep verifying contained runs actually executed (a probe that silently no-ops
looks identical to a fast, successful run).

`run_serial` (in `contained.py`) additionally holds an `flock` for the
whole call (never two model processes at once) and re-checks
`/proc/meminfo`'s `MemAvailable` **after** acquiring the lock, not before —
checking before the lock races a concurrent job that is about to free RAM
on exit.

### Two binaries, one canonical checkout

`larql` (the CLI) and `larql-server` (the process `larql serve` execs into)
must **both** be built from the same canonical `larql-canonical` checkout.
`cargo build -p larql-cli` alone is insufficient — `larql serve` will fail
to find `larql-server` unless it was built alongside it (`cargo build
--release` at the workspace root, or explicitly `-p larql-server` too).
`bin/coding-agent` documents this in its `LARQL_BIN` comment.

### Known limitations (with issue refs)

- **`larql serve` panics on f16 / `quant=none` vindexes** —
  **larql-to-sparql#253** ("attn Q4K slices missing for layer 0"). CLI
  `INFER` works fine on the same vindex; only the HTTP server's chat route
  panics. **Do not route f16 or patched vindexes through `larql serve` /
  `bin/coding-agent`'s larql path.** Use `scripts/pipeline/chat_shim.py`
  instead — it never calls `larql serve`; it opens a `cli-lql` repl session
  per request and issues APPLY PATCH + INFER through the CLI. This is why
  the demo-matrix's chat cells (server-backed) errored 12/12 while the
  chat-shim-backed goose demo worked: they are two different serving
  paths, not the same path in two moods.
- **`REMOVE PATCH` cannot match a `BEGIN`/`SAVE`-created patch** —
  **larql-to-sparql#252**. `exec_save_patch` writes `description: None`
  while `exec_remove_patch` matches on `description == path`, so a patch
  saved via `BEGIN PATCH ...; ...; SAVE PATCH;` can never be removed by
  `REMOVE PATCH` afterward. **Rollback in this codebase is: never apply the
  rejected step's patch, and delete its `.vlp` file** — never issue
  `REMOVE PATCH` expecting it to undo a saved patch. `scripts/pipeline/
  induct.py`'s halt-on-failure path implements this correctly; follow that
  pattern, don't add new `REMOVE PATCH` call sites.

### Patched serving (the chat path)

`scripts/pipeline/chat_shim.py` is the OpenAI-compat surface for
patched/f16 vindexes: each `POST /v1/chat/completions` opens one `cli-lql`
repl session, `APPLY PATCH`es the configured overlay chain (empty in
`--no-patches` ablation mode), then issues one canonical-template `INFER`,
so ablation is a data difference (empty patch list), not a separate code
path that could silently diverge. It handles SSE streaming (goose always
requests `stream: true` and aborts on a plain JSON body) and canonicalizes
goose's `<info-msg>` prompt wrapper before treating the message as the
INFER prompt. `scripts/run_goose_demo.sh` is the process-lifecycle wrapper
around it (see Server lifecycle above).

## Build, test, run

Python:
```bash
python3 -m pytest tests/pipeline/          # 145 tests, no model loads, no containment needed
```

Shell:
```bash
bash tests/test-coding-agent.bash          # 13 assertions, mocks only
bash tests/test-pi-harness.bash            # 14 assertions, mocks only
bash tests/test-agent-battery.bash         # 17 assertions, mocks only
bash tests/test-goose-demo.bash            # 16 assertions, mocks only (no live shim/model)
```
All four bash suites run entirely against `tests/mocks/{pi,goose,curl,larql,ollama,sudo,systemctl}`
and never start a real model or server — they need no `larql-probe` wrapping.

End-to-end pipeline (each live/model-loading step needs `larql-probe safe`
containment; see Operational laws: Containment):

```bash
# 1. Extract this (or any git) repo into a graph — pure Python, no containment needed
python3 scripts/extract_repo.py <repo_root> --out pipeline-out/

# 2. Extract a base vindex from a model checkpoint — CONTAINED (model load)
larql-probe safe --mem 3000 --cpus 6 -- \
  <larql-bin> extract-index <checkpoint> --out smollm2-360m-canonical.vindex

# 3. Build n=1 base-case patches + freeze calibration — CONTAINED (drives larql repl)
python3 scripts/run_base_cases.py --bin <larql-bin> --vindex <vindex> --out <artifacts-dir>
# (run_base_cases.py shells out through scripts/pipeline/contained.py; no separate
#  larql-probe invocation needed from the caller)

# 4. Run finite induction over the graph — CONTAINED, long-running (budget-bound; use setsid)
setsid nohup python3 scripts/run_induction.py --bin <larql-bin> --vindex <vindex> \
  --graph pipeline-out/graph.larql.json --artifacts <artifacts-dir> \
  --n-max 64 --budget-s 10800 &   # detach: turn-end teardown has killed foreground runs before

# 5. Run the demo matrix (graph-file / browse / infer / chat cells) — CONTAINED
python3 scripts/run_matrix.py --bin <larql-bin> --vindex <vindex> \
  --graph pipeline-out/graph.larql.json --artifacts <artifacts-dir> --limit 12

# 6. Goose demo (patched vs. ablation, via chat_shim) — CONTAINED, needs `goose` installed
scripts/run_goose_demo.sh --bin <larql-bin> --vindex <patched-vindex> \
  --patches-dir <artifacts-dir>/induction --questions questions.txt \
  --expected expected.txt --out <out-dir> --port 8282
```

Long-running live steps (4–6) must be `setsid`-detached with on-disk logs
before any agent turn ends — a turn-end teardown has killed foreground live
jobs mid-run more than once (progress.md, "ANOMALY #2", "ANOMALY pattern
#3"); this is an environmental hazard of this harness, not a code bug, and
is mitigated by detaching, not by holding the turn open.

## Architectural invariants

- **Deterministic extraction.** `scripts/pipeline/path_edges.py` and
  `corpus.py` derive edges only from git-tracked paths and file content;
  the same repo state always produces the same edge set (dedup keyed by
  `(s, r, o)`, canonical sort order). Parse failures are quarantined
  (`coverage.json`'s `quarantine`), never silently dropped.
- **Strict-v2 λ layering.** `scripts/pipeline/lam.py`'s `LayerMap.layer()`
  maps graph depth to model layer as `k_lo + (depth - 1)`; a chain deeper
  than the layer band (`k_hi - k_lo + 1`) is `None` — **quarantined, never
  compressed** into an existing layer, because the layer axis is a strict
  topological order and `AT LAYER` is single-layer-only. Attribute-only
  nodes and KNN-stratum edges use `default_layer()` (`k_hi - 1`) instead,
  since they are non-chaining retrieval keys exempt from strictness. In the
  induction run that actually executed (`induct.py`, KNN/cli-lql
  calibration), **every edge inserts at the default layer**
  (`k_hi - 1`); λ is still computed per edge but recorded only as
  certificate metadata — strict-λ placement governs the COMPOSE stratum,
  which this induction run does not exercise.
- **Finite induction `I(n)` with non-application rollback.** `induct.py`
  inserts one reversible patch per edge and machine-checks, per step:
  insert succeeded, every inserted edge so far is DESCRIBE-retrievable,
  canonical-template INFER fires the KNN override with the correct target
  for both the new edge and a sample of priors (a wrong-target override is
  an unconditional failure — a collision), and the fixed canary set's
  top-token distribution is conserved within tolerance. No step's success
  is inferred from its predecessors; on failure, the step's `.vlp` is
  deleted (rollback = non-application; see #252 above) and a halt
  certificate is written. **The current frontier is n=18, zero collisions
  across every sampled generation check — reached because the wall-clock
  budget ran out (external-kill), not because the edge sequence was
  exhausted.** Treat n=18 as "certified so far, budget-bound," never as
  "the induction is complete" or "18 is a capacity ceiling."
- **Ablation-controlled demos.** Every demo capability (browse/infer cells
  in `matrix.py`, the goose demo in `run_goose_demo.sh`) is reported only
  when the patched target hits AND the unpatched ("ablation") twin misses
  the same probe — `confounded` (both hit) and `absent` (neither hits) are
  distinct, explicitly-labeled outcomes, never conflated with a genuine
  vindex-attributed pass.

## Where to find things

- Session ledger of what has actually run (vs. what's implemented but
  unverified): [.superpowers/sdd/progress.md](.superpowers/sdd/progress.md)
- Design rationale: [docs/specs/2026-06-23-design.md](docs/specs/2026-06-23-design.md)
- Operability findings that produced this file:
  `~/docs/2026-07-02-babel-harness-operability-ux.md` (outside this repo;
  the work-package source for the operational laws above)
- LQL output-format parsing, pinned against `larql-canonical` Rust source
  (not spec prose): [scripts/pipeline/lql_driver.py](scripts/pipeline/lql_driver.py)
- Upstream LARQL project structure/build/invariants:
  `~/work/larql-canonical/AGENTS.md`
