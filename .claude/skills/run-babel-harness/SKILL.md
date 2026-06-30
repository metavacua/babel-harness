---
name: run-babel-harness
description: Run, test, and drive the babel-harness binaries (pi-harness, coding-agent). Use when asked to run babel-harness, test the harness, exercise harness binaries, or verify the smoke suite.
---

# run-babel-harness

babel-harness provides three binaries under `bin/`: `pi-harness` (agentic task runner), `coding-agent` (Goose-based coding agent with larql/OpenRouter routing), and `demo-coding-agent`. The primary smoke driver is `.claude/skills/run-babel-harness/smoke.sh`.

## Quick start

```bash
export PATH="$HOME/babel-harness/bin:$PATH"
cd "$HOME/babel-harness"
bash .claude/skills/run-babel-harness/smoke.sh
```

All 7 checks pass on a healthy machine. The larql serve check takes ~15s (weight pre-load).

## Prerequisites

- `OPENROUTER_API_KEY` set in env (or Ollama running locally for fallback)
- `larql` binary at `$HOME/larql/target/release/larql` (for `--model larql/...`)
- `larql-server` binary at `$HOME/larql/target/release/larql-server`
  - Build: `cd ~/larql && cargo build --release -p larql-server`
- Full vindex at `$HOME/larql-vindexes/smollm2-360m.vindex` (for larql serve test)

## Binaries

### pi-harness

Routes a task through OpenRouter (primary) or Ollama (fallback). Output is JSONL.

```bash
pi-harness --status                          # health check: OpenRouter + Ollama
pi-harness "output the word SMOKEPASS"       # run a task
```

Parse the reply from `agent_end.messages[-1].content[].text`:

```bash
pi-harness "output the word SMOKEPASS" > /tmp/out.jsonl
python3 -c "
import sys, json, pathlib
for line in pathlib.Path('/tmp/out.jsonl').read_text().splitlines():
    obj = json.loads(line)
    if obj.get('type') == 'agent_end':
        for msg in reversed(obj['messages']):
            for b in msg['content']:
                if b['type'] == 'text':
                    print(b['text']); exit(0)
"
```

Output confirmed: `SMOKEPASS`

### coding-agent

Wraps Goose; routes to OpenRouter or local larql-server.

```bash
coding-agent "print hello world in python"                    # auto: OpenRouter
coding-agent --model larql/smollm2-360m "print hello world"  # force larql local
```

`--model larql/<vindex-name>` starts `larql serve` on `$LARQL_PORT` (default 8181), then routes Goose at it via OpenAI-compat API.

### demo-coding-agent

```bash
demo-coding-agent
```

Runs the bundled demo task.

## Test suite

```bash
bash tests/test-coding-agent.bash    # 41 unit tests — all mock, no network
bash tests/test-larql-graft.bash     # 4 integration tests (2 need LARQL_BIN + GH CLI)
```

## Smoke driver

`.claude/skills/run-babel-harness/smoke.sh` exercises all seven checks:

| # | Check | Notes |
|---|---|---|
| 1 | `pi-harness --status` | At least one provider reachable |
| 2 | `pi-harness` task round-trip | Confirms JSONL output + reply extraction |
| 3 | `test-coding-agent.bash` | 41/41 pass |
| 4 | `ddt_proof.py` | Verdict: PROVEN |
| 5 | `test-larql-graft.bash` | 4/4 pass |
| 6 | `larql serve` | Starts on port 8282, smollm2-360m.vindex |
| 7 | `coding-agent --model larql/*` | Goose uses larql OpenAI-compat base URL |

Override env vars:

```bash
LARQL_PORT=9000 \
LARQL_VINDEX=$HOME/larql-vindexes/qwen3-0.6b.vindex \
bash .claude/skills/run-babel-harness/smoke.sh
```

## Gotchas

- **pi-harness pipeline hang in subshells**: Piping `pi-harness` output through a heredoc-based Python parser hangs. Write to a tmpfile first, then parse (`python3 script.py /tmp/file`).
- **larql-server needs a full vindex**: The babel-harness coding-agent vindex (`build/babel-harness-coding-agent.vindex`) is a gate/down-only slice — it lacks `attn_weights.bin` and tokenizer files. `larql-server` requires the full vindex (smollm2-360m.vindex).
- **larql-server via symlink**: `larql serve /path/to/symlink` fails with `Parse("No such file or directory")`. Use the real path.
- **larql serve startup**: Weight pre-load for smollm2-360m takes ~10-15s. Poll up to 60s, not 30s.
- **coding-agent `--model ollama/...`**: Routes to OpenRouter, not local Ollama — the `ollama/` prefix is not special. Use `larql/` to force local inference.
