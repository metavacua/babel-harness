# Babel Harness

Claude Code as full-stack operator of a local AI coding pipeline.

Routes coding tasks through:
- **OpenRouter free-tier** (default, remote)
- **Ollama** (offline fallback, local)
- **larql vindex** (custom compiled models via [metavacua/larql-to-sparql](https://github.com/metavacua/larql-to-sparql)) — a working, running pipeline, not a design sketch: `scripts/` + `scripts/pipeline/` extract a git repository into a graph, map it onto model layers, and drive `larql repl`/`larql serve` under containment to insert, verify, and query knowledge inside a compiled vindex.

Claude Code configures, repairs, and delegates to the [Pi agent](https://pi.dev) harness, which handles provider routing inside a resource-aware cgroup sandbox.

## Status

The pipeline runs end to end against a real vindex, not just in tests:

- **Finite induction certified to n=18** on `smollm2-360m-canonical` — 18
  reversible patches, each machine-checked (insert succeeded, every prior
  edge stays DESCRIBE-retrievable, canonical-template INFER fires the
  correct KNN override, canary set conserved) — zero collisions across
  every sampled generation check. The run stopped because its wall-clock
  budget ran out (external-kill), not because the edge sequence was
  exhausted: **n=18 is a demonstrated frontier, not a completeness claim.**
- **Goose chat demo working via a custom OpenAI-compat chat shim**
  (`scripts/pipeline/chat_shim.py`), which sidesteps `larql serve`'s
  inability to serve f16/patched vindexes (see AGENTS.md). A live session
  confirmed goose returns the exact inserted edge end-to-end; the
  self-contained two-pass runner (`scripts/run_goose_demo.sh`, patched vs.
  ablation) is committed and unit-tested (16/16) but its own durable live
  re-run is still pending.
- **OpenRouter/Ollama paths** are exercised via `bin/coding-agent` and
  `bin/pi-harness` as before.

See [AGENTS.md](AGENTS.md) for the full operational picture: workspace
layout, server-lifecycle rules, containment policy, known upstream bugs
(larql-to-sparql#246/#252/#253) and how this pipeline works around them,
and how to build/test/run each stage.

## Licensing

This project uses three licenses:

| Scope | License |
|-------|---------|
| Documentation (`docs/`, plus root `README.md`/`AGENTS.md`/`CLAUDE.md`) | [CC-BY-SA-4.0](LICENSES/CC-BY-SA-4.0.txt) |
| Software (`bin/`, `scripts/`, `src/`) | [AGPL-3.0-or-later](LICENSES/AGPL-3.0-or-later.txt) |
| Permissive/upstream-compatible components (`compat/`, `upstream/`) | [Apache-2.0](LICENSES/Apache-2.0.txt) |

REUSE-compliant. See [`.reuse/dep5`](.reuse/dep5) for per-directory declarations.
