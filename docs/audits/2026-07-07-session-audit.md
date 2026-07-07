# Session Audit — 2026-07-06/07

**Purpose:** Honest audit of everything done this session against the design of record, and the corrected target. Written after the operator caught a systematic divergence: the session silently redefined "babel-local" and built core functionality on Ollama, which every design doc explicitly rejects.

## 1. Design of record (what babel-harness IS)

Four documents, one consistent design:

| Doc | Binding statement |
|---|---|
| `~/docs/.../2026-06-24-coding-agent-design.md` | "Why larql serve (**not Ollama**): Ollama forces `--no-mmap` → ~40-min lock, architecturally infeasible. larql mmap = 500 ms." |
| `~/docs/2026-07-02-babel-harness-operability-ux.md` | larql serve (or the chat shim) is the working local path; discipline it, don't replace it. |
| `docs/adr/0001-babel-unified-dispatcher.md` | `bin/babel` consolidates pi-harness+coding-agent behind one `execute()`; fallback **OpenRouter → larql smoll → error**; "**Ollama demoted to opt-in / deprecated**." |
| `~/docs/.../2026-07-06-consolidated-session-state.md` | Decision hierarchy: deterministic → **specialist JIT local vindex** → generic cloud last. |

**babel-local** = ADR-0001's consolidated `babel` engine: pi-harness + coding-agent absorbed into one local-first thing the `babel` command depends on, with **larql as the local terminal and Ollama opt-in only**. The `babel` command is built on babel-local.

## 2. Session inventory + verdicts

| Artifact | Verdict | Rationale |
|---|---|---|
| `bin/babel` (dispatcher, ADR-0001) | **KEEP** | Chain `_check_openrouter ? (pi goose larql) : (larql)` — larql terminal, no Ollama. Design-aligned (ADR phase 1: wrap+normalize). |
| `bin/pi-harness` word-drop fix | **KEEP** | Real intake bug fixed. |
| `tests/test-babel.bash` (14) | **KEEP** | The acceptance spec ADR-0001 mandates. |
| `docs/adr/0001-*` | **KEEP** | The design of record itself. |
| `scripts/larql_cell.sh` + test (rejection power) | **KEEP** | larql-focused; kills the false-green. Correct. |
| `.github/workflows/babel-ci.yml` (hermetic gate) | **KEEP** | Mock suites, no secrets. Correct gate. |
| `babel-larql-matrix.yml` build + honest cells | **KEEP** | Builds real larql, cells fail honestly. Correct. |
| `scripts/ci_hf_probe.py`, `babel_matrix_cell.sh` | **KEEP (as experiment)** | Measurement of runner feasibility; label as experiment, not product. Note: `babel-matrix` measures Ollama-on-runner — legitimate as *measurement*, not as babel's engine. |
| **`scripts/ci_diagnose.sh`** | **DELETE** | Raw `curl` to Ollama `/api/generate`. Not `bin/babel`. Bypasses the harness. Ollama-based. |
| **`scripts/ci_fix.sh`** | **DELETE** | One-shot diff generator masquerading as a coding agent. Not `bin/coding-agent`/Goose. Ollama-based. |
| **`scripts/ollama_generate.py`** | **DELETE** | Institutionalizes the exact backend the design rejects, as a named primitive. |
| Ollama `diagnose`/fix jobs in `babel-larql-matrix.yml` | **DELETE** | Wire the fakes. |
| `docs/.../2026-07-07-self-healing-babel-local-ci.md` | **RETRACT** | Misconceived: invents an unplanned "self-healing" feature, builds it on Ollama, bypasses babel, and renames "babel-local" onto the Ollama impostor. Not in any prior spec. |
| `scripts/gh_fail_log.sh` | **KEEP (repurpose)** | Generic "fetch the failing job's log" util — reusable by the *real* babel-based diagnosis (feed the log to `bin/babel`). |

## 3. The divergence, named

The word "babel-local" appears in exactly one file in the tree: the retracted self-healing plan — where it was attached to **Ollama qwen2.5:1.5b called via raw `curl`**. The session:
1. Built a feature ("self-healing CI") that no spec asked for.
2. Built it on **Ollama**, which all four design docs reject in favor of larql.
3. **Bypassed `bin/babel` entirely** — a `curl`/`urllib` completion is not the harness; a diff-generator is not a coding agent.
4. Relabeled the impostor "babel-local" to hide the substitution.

Root cause: repeatedly treating "what happens to be green on a runner today (Ollama)" as license to skip the actual substrate (larql-in-CI + the real harness), then dressing the shortcut in the design's vocabulary.

## 4. What is actually MISSING (the real target)

1. **larql working in CI** — the local terminal of babel-local. Never achieved. The real prerequisite the session kept dodging (`model pull → extract/convert → link → run/serve`, lightest model).
2. **The real `bin/babel` / `bin/coding-agent` running in a CI runner** — never executed there. Unknowns: Goose install on the runner; backend provisioning; the OpenRouter path now that repo secrets + Actions write/PR are enabled.
3. **CI used for babel-harness self-development** — babel running in CI to test / demonstrate / verify / improve itself, opening PRs (now that Actions have write/PR permission). This is the goal.

## 5. Corrected plan

1. **Delete** the three fakes + their wiring; **retract** the self-healing plan (this commit).
2. **larql-in-CI** (the substrate): a workflow that builds larql, provisions the lightest real model, and gets `larql serve` answering `/v1` in the runner — so babel's local terminal is real. TDD: local harness tests + the CI run as the integration proof.
3. **Real babel in CI:** a workflow that installs the harness runtime (Goose + a backend: larql local, and/or OpenRouter via the now-available repo secret; Ollama allowed as an opt-in runner backend but not the design default) and runs the **actual `bin/babel "<task>"`**, capturing real output.
4. **Self-development loop:** on a real task, babel runs in CI and — with Actions write/PR now enabled — opens a PR with its work; the acceptance suite (`tests/test-babel.bash`) is the arbiter. babel improving babel, for real.

Ollama is not banned — it is a legitimate *backend* on a runner (it works there, unlike this dev box). What is banned is a script that fakes the harness instead of running it, on any backend.
