---
name: deep-research-babel
description: >-
  Deep, multi-source research routed through the babel-harness binaries
  (pi-harness / coding-agent) instead of Claude/Anthropic cloud subagents, so
  inference cost is delineated to babel. The harness performs grounded web
  fetching (real URLs, real text); babel performs the well-defined cognition on
  that provided text — claim extraction, synthesis, and adversarial
  verification — and the skill emits a cited report. Use for ANY deep research,
  literature review, multi-source investigation, or comparative-analysis request
  where the operator wants the work done on babel rather than cloud subagents.
  Triggers on: "deep research", "research X", literature review, multi-source
  investigation, "use babel to research", comparative research, fact-check with
  sources. This is the babel sibling of the cloud `deep-research` skill; prefer
  it in this project by default.
---

# deep-research-babel

Deep research where the **inference is done by babel**, not by Claude cloud
subagents. This skill exists because the cloud research pathways (`deep-research`,
`research-phase`) route fan-out to web + Claude and never touch babel. Here babel
is the executor by construction, and that routing is **load-bearing and
test-guarded** — see `smoke.sh` — so it cannot be silently downgraded to an inert
mention.

## Why hybrid (grounded-fetch + babel-cognition)

Empirical finding (2026-07-06 comparative run): handed a read-and-propose task,
the free-tier babel substrate **fabricated file contents and falsely claimed to
have acted**. For a research tool, the analogous failure is inventing citations —
the worst possible outcome. Therefore:

- **The harness does the grounding.** Real `WebSearch` / `WebFetch` (or
  `scripts/`-based fetchers) produce actual URLs and actual source text. babel is
  never asked to *find* or *recall* a source — only to reason over text it was
  handed. This confines babel to its reliable tier ("well-defined work on
  provided context") and structurally prevents fabricated citations.
- **babel does the cognition.** Claim extraction, cross-source synthesis, and
  adversarial verification run on `pi-harness` / `coding-agent`.
- **Every babel call is guarded.** Zero tool/'grounding' signal = delegation
  failure (surface loudly). A claim that does not resolve to a passed-in source
  snippet is dropped, not reported.

## Prerequisites

```bash
export PATH="$HOME/babel-harness/bin:$PATH"
pi-harness --status        # at least one provider reachable before starting
```

## Procedure

### Phase 0 — Frame (inline, cheap)
Decompose the question into 3–8 answerable sub-questions. Decomposition is
low-token judgment; keep it inline. Record sub-questions as an explicit list —
they are the fan-out work-list.

### Phase 1 — Ground the sources (HARNESS, not babel)
For each sub-question, gather real sources with the harness's own tools
(`WebSearch` → `WebFetch`), NOT babel. Save each fetched source as
`{id, url, fetched_text}` to the scratchpad. **babel never invents a URL** — it
only ever sees `fetched_text` you retrieved.

### Phase 2 — Extract claims (BABEL)
For each fetched source, hand babel the text and ask for grounded claims:

```bash
pi-harness "From ONLY the following source text, extract atomic factual claims \
relevant to: <sub-question>. Quote the exact supporting sentence for each claim. \
If the text does not support a claim, output nothing. SOURCE:\n<fetched_text>"
```

Guard: after each call, check the reply. **No quoted supporting sentence =
discard the claim.** Zero-output on substantive text is a delegation anomaly —
re-run or fall back to inline extraction.

### Phase 3 — Adversarially verify (BABEL, independent)
For each surviving claim, run an independent babel call prompted to REFUTE it
against the same source text (default to "unsupported" when uncertain):

```bash
pi-harness "You are a skeptic. Here is a CLAIM and its SOURCE text. Does the \
source actually support the claim, verbatim? Answer SUPPORTED or UNSUPPORTED and \
quote the decisive sentence. Default to UNSUPPORTED if the quote is not present. \
CLAIM: <claim>\nSOURCE:\n<fetched_text>"
```

Keep only claims returned SUPPORTED with a real quote.

### Phase 4 — Synthesize (BABEL) + citation check (HARNESS)
Hand babel the set of verified {claim, url, quote} tuples and ask for a synthesis
report. Then the harness verifies every citation in babel's draft resolves to a
tuple that was actually passed in — **any citation not traceable to a fetched
source is stripped**, and its absence noted in the report.

### Phase 5 — Report
Emit the cited report to the scratchpad (and, if a durable deliverable is wanted,
via the `scholarly-white-paper` skill). Every claim carries {url, verbatim quote}.

## Failure handling

- `pi-harness --status` shows no provider → `pi-harness --repair`, else fall back
  to `--model larql/...` (LOCAL inference — MUST be wrapped per the host
  containment policy: `larql-probe safe -- ...`).
- Repeated babel fabrication on a phase → drop that phase to inline Claude for
  this run and record it, so the report states which cognition was NOT on babel.
  Honesty about substrate substitution is part of the deliverable.

## Comparative-analysis mode

When the operator asks for comparative analysis, run each cognition phase BOTH on
babel and inline, and report divergences (e.g., claims babel dropped that Claude
kept, or fabrications babel introduced). This is how "babel properly developed"
gets measured instead of asserted.

## Invariant (enforced by `smoke.sh`)

The executable research path MUST invoke a babel binary (`pi-harness` or
`coding-agent`). A version of this skill in which babel appears only as prose, a
repo name, or a comment — with no executable invocation — is a regression and
fails the smoke test.
