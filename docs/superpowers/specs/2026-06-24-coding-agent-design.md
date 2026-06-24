# Design: Lightweight Coding Agent — OpenRouter + larql Local Inference

**Date:** 2026-06-24  
**Status:** Approved (auto-mode, awaiting no objection)  
**Repo:** babel-harness  

---

## Background and Constraints

The target machine is a low-RAM CPU-only host. The previous
approach (Ollama + qwen2.5-coder:7b, 4.7 GB) was architecturally infeasible:
Ollama forces `--no-mmap` for CPU-only inference, causing a full eager read
into RAM that locks the terminal for ~40 minutes. The system rebooted on
the first attempt; the second kept cgroup enforcement stable but still locked.

larql v0.1.0 is already built and exposes a
full OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`,
`/v1/embeddings`) via `larql serve`. The `smollm2-360m` vindex (860 MB, F16,
32 layers) is already linked and loads via mmap in < 500 ms — three orders of
magnitude faster than the Ollama 7 B path.

Goose v1.36.0 is installed and configured with OpenRouter as its active
provider. Its `developer` extension (enabled) provides read/write/bash tool
use — full coding agent capabilities without additional scaffolding.

---

## Goal (Two Sub-projects)

### Sub-project A — Comparative Analysis (research only)

Audit where larql reimplements or DIYs functionality that already exists under
Apache-2.0 or MIT licenses, or where an existing project could serve as a
reference implementation. Output: `docs/superpowers/specs/2026-06-24-larql-comparison.md`.

### Sub-project B — Coding Agent (implementation)

Deliver a working `bin/coding-agent` script that:

1. Routes to OpenRouter (primary) when reachable.
2. Falls back to `larql serve smollm2-360m` (instant-start local inference)
   when OpenRouter is unreachable.
3. Invokes **Goose** as the agent in both cases — same agent framework,
   different provider configuration.
4. Includes a `bin/demo-coding-agent` that shows timing of both paths.

---

## Architecture

```
User: coding-agent "TASK"
          │
          ├─ OpenRouter reachable?
          │       YES → GOOSE_PROVIDER=openrouter GOOSE_MODEL=<free-model>
          │              goose run -t "$TASK"
          │
          └─ NO → ensure larql serve running at $LARQL_PORT
                  → GOOSE_PROVIDER=openai
                    OPENAI_BASE_URL=http://localhost:$LARQL_PORT/v1
                    OPENAI_API_KEY=larql
                    GOOSE_MODEL=smollm2-360m
                    goose run -t "$TASK"
```

**Why Goose (not pi-agent for the local path):**
- Goose v1.36.0 already accepts `GOOSE_PROVIDER=openai` + `OPENAI_BASE_URL`
  env vars — tested and confirmed to connect to localhost:8080.
- `developer` extension provides full read/write/bash tool loop without
  additional scaffolding.
- Same agent framework for both paths means consistent behaviour.
- pi-harness (existing) is unchanged and kept for backward compatibility
  with the OpenRouter→Ollama routing path.

**Why larql serve (not Ollama):**
- larql uses mmap: model pages fault in on demand, no eager load.
- Startup time: ~500 ms vs ~40 min for 7 B with Ollama's forced eager load.
- smollm2-360m (360 M params, 860 MB) fits comfortably in the target machine's RAM.
- larql serve exposes the same OpenAI-compat surface Goose expects.

---

## Components

### `bin/coding-agent` (new)

Responsibilities:
- Accept a task string as the first argument.
- Accept `--model` override (format: `provider/model` or `larql/vindex-name`).
- Check OpenRouter reachability (`/api/v1/models`, 3 s timeout).
- If larql path: start `larql serve` in background if not already listening,
  wait for `/v1/models` response (polls max 5 s, not 40 min).
- Invoke `goose run -t "$TASK"` with correct provider env vars.
- Emit `coding-agent: provider=<name> model=<model>` to stderr.
- Output: whatever goose emits (not normalized to JSON — goose's native output
  is rich enough for the demo).

Seams (testable env vars):
- `OPENROUTER_CHECK_URL` (default: `https://openrouter.ai/api/v1/models`)
- `LARQL_BIN` (default: `$HOME/larql/target/release/larql`)
- `LARQL_PORT` (default: `8080`)
- `LARQL_VINDEX` (default: `smollm2-360m`)
- `OPENROUTER_MODEL` (default: `qwen/qwen3-235b-a22b:free`)
- `GOOSE_BIN` (default: `goose`)

### `tests/test-coding-agent.bash` (new)

Four tests using the same mock-curl pattern as `test-pi-harness.bash`:

- **Test 1**: OpenRouter reachable → goose invoked with `GOOSE_PROVIDER=openrouter`.
- **Test 2**: OpenRouter down → larql server checked/started → goose invoked
  with `GOOSE_PROVIDER=openai` and `OPENAI_BASE_URL` set.
- **Test 3**: larql already running (health check passes) → no `larql serve`
  spawned.
- **Test 4**: `--model larql/smollm2-360m-clustered-v2` override forces larql
  path regardless of OpenRouter state.

Mocks needed: `curl`, `goose`, `larql` (same directory as existing mocks).

### `bin/demo-coding-agent` (new)

30-line bash script:
1. Runs `coding-agent "write a Python function that returns the nth Fibonacci number"` with OpenRouter.
2. Shows elapsed time and provider.
3. Sets `OPENROUTER_CHECK_URL=http://mock-offline.invalid` to force larql path.
4. Runs again, shows elapsed time (expected < 2 s from cold start of larql serve).

---

## What Does NOT Change

- `bin/pi-harness` — unchanged; the OpenRouter→Ollama path is kept as-is.
- `tests/test-pi-harness.bash` — all 14 tests remain green.
- Cgroup enforcement — larql serve PID enrolled in babel-harness cgroup the
  same way Ollama was. larql's mmap means RSS only grows as tokens are
  generated, not at startup.

---

## Testing Strategy (TDD)

All four tests are written **before** any implementation code per TDD protocol.
Mocks follow the existing pattern in `tests/mocks/`:
- `curl`: controlled by `MOCK_CURL_OPENROUTER_EXIT` and `MOCK_CURL_LARQL_EXIT`.
- `goose`: records invocation with provider env vars to `MOCK_CALL_LOG`.
- `larql`: health-check mock returns 200 or ECONNREFUSED based on
  `MOCK_LARQL_RUNNING`.

---

## Sub-project A Scope (Comparative Analysis)

Dimensions for the comparison, to be filled in by deep-research:

| Dimension | larql component | Analogues (Apache-2.0 / MIT) | Verdict |
|---|---|---|---|
| Serving + OpenAI compat | `larql-server` | llama.cpp/server, Ollama, vLLM | TBD |
| Model loading | mmap vindex | llama.cpp mmap, transformers mmap | TBD |
| Format conversion | `larql extract` | llama.cpp quantize, safetensors | TBD |
| Tokenisation | larql tokenizer | HF tokenizers, tiktoken | TBD |
| KV cache | `larql-kv`, residual codec | FlashAttention, vLLM PagedAttention | TBD |
| CLI surface | 22+ verbs | Simon Willison `llm`, `mlx-lm`, `transformers-cli` | TBD |
| Embeddings | `/v1/embeddings` | sentence-transformers, text-embedding-inference | TBD |
| Mechanical interp | `larql dev` 26 subcommands | TransformerLens, baukit, nnsight, pyvene | TBD |
| Coding agent framework | (none yet) | Goose, Aider, Open Interpreter, Continue | TBD |
| Knowledge RAG | vindex gate KNN | LlamaIndex, Chroma, Qdrant | TBD |
| Self-hosted coding assistant | (none yet) | Tabby (TabbyML), Continue | TBD |

**Forks in scope**: metavacua has development forks of tabby, pi-agent, and
goose — the comparison should note where these forks add integration hooks that
the upstream projects lack.

---

## Demo Success Criteria

1. `coding-agent "write a Python function that returns the nth Fibonacci number"` completes.
2. OpenRouter path: response in < 10 s (network-dependent).
3. larql local path: `larql serve` starts in < 2 s, first token in < 30 s for
   smollm2-360m (360 M params, CPU decode, ~3 tok/s estimated).
4. All tests in `test-coding-agent.bash` pass.
5. All 14 existing `test-pi-harness.bash` tests still pass (no regression).
6. Comparison doc covers all 11 dimensions with evidence-backed verdicts.
