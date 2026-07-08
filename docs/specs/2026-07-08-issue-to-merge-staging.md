<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Issue → PR → Merge: end-to-end staging for CI-based coding agency

**Status:** Draft (2026-07-08)
**Derives from:** ADR-0001, the 2026-07-07 session audit, and the evidence of three CI
runs: 28910097525 (garbage PR near-miss), 28970562690 (first closed loop → PR #27),
28970562526 (larql cells running real inference).
**Scope:** the *harness*. Model quality is explicitly out of scope: the model is treated
as an untrusted noise generator, and every anomaly it produces is free instrumentation
of the harness. A stage is "good" iff a garbage model cannot make it lie and a good
model is not blocked by it.

## The two invariants everything below instantiates

1. **Measurement must be pristine.** Anything that scores, gates, or reports must be
   snapshotted/isolated *before* the agent gets write access to the tree. Violations
   observed: working-tree grader (fixed: `/tmp/merge_ladder.pristine.sh`), rung-3
   suites depending on in-tree mocks the agent can (and did) corrupt.
2. **Signals must fail closed and never contradict observable state.** Whitelisted
   exit contracts (0/10/11; anything else is RED), positive completion signals
   (assistant-message presence, not absence-of-error-strings), and side-effect
   reporting (an `ok:false` envelope next to a completed target edit is a
   contradiction consumers cannot resolve — observed in run 28970562690).

## Stages, with necessary vs. sufficient capabilities

Legend: **N** = necessary for the stage to function at all; **S** = sufficient for the
stage's output to be *trusted* without human re-derivation. ✅ = exists and CI-tested
today, 🟡 = partial, ❌ = missing (issues filed 2026-07-08: #28 degrade-detection, #29 rung-2 shebang, #30 larql-terminal provisioning, #31 envelope/side-effects, #32 PR/branch lifecycle — #28/#29 carry fixes in this branch).

### S0 — Issue authoring (the work order)
| Cap | N/S | Status |
|---|---|---|
| Issue cites concrete `path[:line-range]` targets that exist in the checkout | N | ✅ enforced downstream by discovery; #19 conforms |
| Issue body treated as *data*, never as harness control input (sentinel text in a body poisoned `_needs_degrade`; prompt-injection is the same channel) | S | 🟡 fixed for pi/ollama degrade (#28); goose sniff still content-sensitive (#19's remit) |
| Machine-checkable acceptance criterion (a regression test the fix must turn green) | S | ❌ — the single biggest missing oracle (see S3) |

### S1 — Discovery & task construction (deterministic, no model)
| Cap | N/S | Status |
|---|---|---|
| Fetch issue, extract targets, emit bounded context under the model's tool-calling budget | N | ✅ `issue_context.sh` (CTX_CAP; probe-measured cliff) |
| Declared targets exported machine-readably for later oracles | N | ✅ `TARGETS_OUT` (normalized, tested) |
| File-creation issues (targets that don't exist yet) | S | ❌ documented limitation — target set is `[ -f ]`-filtered |
| Pristine snapshots taken here (graders, mocks) before the agent runs | N | 🟡 grader snapshotted; mocks/suites not yet |

### S2 — The attempt (the only stage a model touches)
| Cap | N/S | Status |
|---|---|---|
| All chain backends actually provisioned (a dead terminal = guaranteed-fatal envelope) | N | ❌ #30 — babel-issue.yml lacks goose + vindex; larql arm structurally dead there |
| Backend failure detection that content cannot spoof (positive completion signal, not sentinel sniffing) | N | ✅ fixed for pi/ollama (#28, acceptance cases 11–12); 🟡 goose (#19) |
| Side-effects isolated per attempt (a failed backend's partial writes leak into the next attempt's tree today) | S | ❌ #31 |
| Envelope reports tree delta, not just transport (`ok:false` + completed edit observed) | S | ❌ #31 |
| Full transcript captured for component scoring | N | ✅ `/tmp/babel.err` → `component_check.py` |

### S3 — Scoring (deterministic certificates)
| Cap | N/S | Status |
|---|---|---|
| Change detection incl. untracked dirs; exact-match target oracle | N | ✅ merge ladder rungs 0–1 (mutation-tested) |
| Syntax gate sees every changed shell file (pattern + shebang) | N | ✅ rung 2 (#29, fixed after run 28970562690 SKIPped a truncated mock) |
| Suites run pristine, not the agent-mutated copies | S | 🟡 report-only today; exit code is pristine, reports aren't |
| **Per-issue regression oracle**: a test that fails before the fix and must pass after | S | ❌ — without it, "touched the target" is the ceiling of what scoring can certify; a broken-but-on-target edit still earns a proposal (PR #27 is exactly this, honestly labeled) |
| Grader crash ≠ honest halt (whitelisted exit codes; other codes go RED) | N | ✅ |

### S4 — Proposal (branch + PR mechanics)
| Cap | N/S | Status |
|---|---|---|
| Branch/commit/push/PR from CI with correct base (head_ref, not merge ref) | N | ✅ (PR #27 proves it end-to-end) |
| Ladder + component report embedded in PR body & step summary | N | ✅ |
| Dedup: one open proposal per issue; supersede old attempts | S | ❌ #32 — unbounded accumulation (PR #26, #27, 6 autonomous branches) |
| Halted-run branch cleanup | S | ❌ #32 (orphan `babel-issue/19-run-28910097525`) |
| Re-run-safe branch naming | N | ✅ (`-run-<id>-<attempt>`) |

### S5 — Independent verification of the proposal
| Cap | N/S | Status |
|---|---|---|
| CI checks actually run on the proposal ref | N | ❌ **structural**: GITHUB_TOKEN-created PRs trigger no workflows (anti-recursion). Requires a PAT/GitHub-App token, or a dispatcher that runs the hermetic gate against the proposal ref and posts a status (#32) |
| Verification delta vs base quantified (did the proposal make the gate greener, redder, or same?) | S | ❌ |
| Proposal diff bounded to declared targets (any off-target hunk demands escalation, not silence) | S | 🟡 oracle checks presence of a target hit, not absence of off-target damage |

### S6 — Integration (merge / rebase / squash)
| Cap | N/S | Status |
|---|---|---|
| Operator gate (nothing auto-merges) | N | ✅ by design |
| Integration mode semantics for machine PRs: **squash** is the right default — one attempt = one commit, ladder preserved in the squash message; rebase only for stacked attempts on the same issue; merge commits only where provenance of a multi-commit attempt matters | S | ❌ policy undocumented; nothing enforces or records it |
| `Fixes #N` linkage so merge closes the issue and the loop's ledger stays consistent | N | ❌ PR bodies don't reference the issue as a closing keyword |
| Post-merge branch deletion | S | ❌ |
| Merge prerequisites machine-stated (regression oracle green + gate delta ≥ 0 + operator ack) | S | ❌ — this line, once true, is the definition of "good quality CI-based coding agency" |

### S7 — Post-merge feedback (the loop's memory)
| Cap | N/S | Status |
|---|---|---|
| Post-merge hermetic gate on main | N | ✅ babel-ci push trigger |
| Longitudinal metrics: component_check scores, ladder heights, envelope honesty per run | S | ❌ nothing accumulates across runs |
| Failed post-merge gate → revert path | S | ❌ |

## The critical path, stated plainly

The loop *functions* today: S0→S4 executed end-to-end for the first time in run
28970562690 (PR #27), and every certificate along the way was honest. What separates
"functions" from "good quality" is exactly three capabilities, in priority order:

1. **S3's per-issue regression oracle** — turns "touched the target" into "changed the
   behavior the issue names". Everything else is scaffolding around this.
2. **S5's independent verification** — a proposal must be checked by machinery the
   proposing run cannot influence (token/trigger work required).
3. **S2's attempt isolation + honest envelope** — so the signal chain from backend to
   PR body never self-contradicts.

Dedup/supersede/cleanup (S4), integration policy (S6), and longitudinal metrics (S7)
are quality-of-life until scale makes them correctness issues; the three above are
correctness issues now.
