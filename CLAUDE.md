# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Reference the `./AGENTS.md` file for the broader context of this project.

Highest-stakes rules (all detailed in AGENTS.md's "Operational laws"):

- **Containment is mandatory.** Every model-loading command (`larql repl`,
  `larql serve`, any `.gguf` load) MUST go through `larql-probe safe --mem
  MB --cpus N -- <command>`. A PreToolUse hook enforces this — wrap the
  command, don't try to bypass it.
- **Never pattern-kill a server or shim.** `pkill -f 'chat_shim'` matches
  the shell running that very command and self-terminates it (repeated
  exit 144, previously misread as a crash). Kill by the PID captured at
  launch (`server_pid=$!` / `SHIM_PID=$!`), or a bracket-glob
  (`'[c]hat_shim'`) if you must search by name.
- **Two binaries, one checkout.** `larql` and `larql-server` must both be
  built from the same `larql-canonical` checkout, or `larql serve` fails
  to find its server process.
- **`larql serve` panics on f16/patched vindexes (larql-to-sparql#253).**
  Use `scripts/pipeline/chat_shim.py` for f16 or patch-overlaid vindexes
  instead of `larql serve`/`bin/coding-agent`'s larql path.
