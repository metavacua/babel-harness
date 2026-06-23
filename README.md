# Babel Harness

Claude Code as full-stack operator of a local AI coding pipeline.

Routes coding tasks through:
- **OpenRouter free-tier** (default, remote)
- **Ollama** (offline fallback, local)
- **larql vindex** (future, custom compiled models via [metavacua/larql-to-sparql](https://github.com/metavacua/larql-to-sparql))

Claude Code configures, repairs, and delegates to the [Pi agent](https://pi.dev) harness, which handles provider routing inside a resource-aware cgroup sandbox.

## Licensing

This project uses three licenses:

| Scope | License |
|-------|---------|
| Documentation (`docs/`) | [CC-BY-SA-4.0](LICENSES/CC-BY-SA-4.0.txt) |
| Software (`bin/`, `scripts/`, `src/`) | [AGPL-3.0-or-later](LICENSES/AGPL-3.0-or-later.txt) |
| Permissive/upstream-compatible components (`compat/`, `upstream/`) | [Apache-2.0](LICENSES/Apache-2.0.txt) |

REUSE-compliant. See [`.reuse/dep5`](.reuse/dep5) for per-directory declarations.

## Status

Design phase. See [`docs/specs/`](docs/specs/) for the current design document.
