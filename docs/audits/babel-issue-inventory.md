# babel — issue & bug inventory

Produced by `scripts/babel_parallel_debug.sh`: five grounded, single-shot
subtasks delegated **in parallel to the babel harness itself** (pi-harness),
each analyzing one boundary of `bin/pi-harness` + `bin/coding-agent`.
Merged with issues found by direct code-reading (marked [read]).

## Delegated findings (babel-on-babel)

### argparse
- ISSUE: Missing handling for multi-word tasks in `pi-harness` - The line `TASK="$1"; shift` only captures the first word of a task, losing subsequent words if the task contains spaces.
- ISSUE: Inconsistent handling of end-of-options marker in `pi-harness` - It lacks a `--` marker to properly separate options from positional arguments, meaning arguments like `--model "$TASK NAME"` where the task starts with `--` would be mishandled.
- ISSUE: Redundant tailing argument collection in `coding-agent` - The second `while [[ $# -gt 0 ]]` loop after `--` is unnecessary if the first loop's `TASK="${TASK:+$TASK }$1"; shift` handles all positional arguments correctly, including those after `--`.
- ISSUE: Potential for parsing error with `--model` in `coding-agent` if -- is used before --model - The `--` break occurs before the `--model` parsing logic, meaning if `--model` appears after `--`, it will be treated as a task argument, not an option.
- ISSUE: Missing argument validation for `--model` in `pi-harness` - It assigns `$2` to `MODEL_OVERRIDE` without checking if `$2` exists or is empty, which could lead to an empty `MODEL_OVERRIDE` string if `--model` is the last argument.
- ISSUE: Hardcoded error messages in `coding-agent` - Error messages like "coding-agent: unknown option: $1" are hardcoded with the script name, making them less reusable if the script is renamed.

### fallback
- ISSUE: `_select_provider_model` logic is unused - The `coding-agent` block implements its own provider fallback logic, ignoring the function intended for this purpose.
- ISSUE: Ollama is not a fallback option in `coding-agent` - The `coding-agent` logic proceeds to LARQL if OpenRouter fails, skipping any Ollama providers defined in `_select_provider_model`.
- ISSUE: Redundant Ollama check in `_select_provider_model` - `_check_ollama` and `_repair_ollama` appear to have overlapping or identical functionality, leading to potential confusion.
- ISSUE: No explicit fallback for "none none" - The `_select_provider_model` function can return "none none", but the `coding-agent` logic does not have a specific path to handle this outcome gracefully, implicitly assuming LARQL is always the next step.
- ISSUE: Inconsistent exit strategy - The `coding-agent` logic exits with `exit 1` if LARQL server start fails, but `_select_provider_model` simply returns "none none" without a clear error handling mechanism for the caller.

### paths
- ISSUE: `vindex` is passed to `_start_larql_server` and used directly by `larql serve`, but its exact nature (relative/absolute path, model identifier) is not clear from the code, leading to potential ambiguity. - The function comment for `_get_larql_model_id` explicitly states "vindex basename (e.g. "smollm2-360m") differs from server model id (e.g. "smollm2-360m-src"). Using the basename causes a 404". This indicates a critical mismatch in how the `vindex` is interpreted by different parts of the system (server vs. client/Goose).
- ISSUE: `_get_larql_model_id` uses `vindex` only as a fallback for `curl` output, not for constructing the request itself. - If the server is down or returns unexpected data, the `vindex` is used as a last resort, but the logic for how this fallback `vindex` is supposed to be a valid model ID is missing, potentially causing errors if it's not a server-recognized model ID.
- ISSUE: `_run_goose_larql` uses `GOOSE_MODEL="$model_id"` which is derived from `_get_larql_model_id`. - If `_get_larql_model_id` returns a server ID that differs from what "Goose" (another tool, presumably) expects based on the original `vindex`, it will cause errors according to the comment in `_get_larql_model_id`. The code does not ensure a consistent interpretation of the model identifier.
- ISSUE: The `vindex` is passed without any explicit path normalization or validation in `_start_larql_server`. - If `vindex` is intended to be a file path, passing it directly without checking if it's relative or absolute, or if it exists, can lead to runtime errors or unexpected behavior depending on the current working directory when `larql serve` is executed.

### output
- ISSUE: `> /dev/null 2>&1 || true` on `tee` commands - Hides write errors to cgroup files, preventing script failure even if essential setup fails.
- ISSUE: `_run_goose_call` function's `grep` checks operate on `PIPESTATUS[0]` after the `tee` command - `PIPESTATUS` refers to exit codes of commands in the pipe. If `tee` succeeds but the piped command fails, `"${PIPESTATUS[0]}"` will be 0, masking the actual command failure.
- ISSUE: `_run_goose_call` function's `grep` check doesn't consider `stderr` from `grep` - `grep ... 2>/dev/null` suppresses `grep`'s own errors, potentially masking issues with the temp file or input.
- ISSUE: `_run_goose_call` function has inconsistent `rm -f "$_outfile"` execution - The temp file is removed in the `if` block for success *and* at the end for failure, which means if the `grep` condition is met, the file is removed, and execution proceeds to the final `rm -f`, which is redundant but harmless. However, the logic could be clearer.
- ISSUE: `_run_goose_call` returns `"${PIPESTATUS[0]}"` in the `else` block - If the initial `"$@" | tee "$_outfile"` fails, `${PIPESTATUS[0]}` will contain the exit code of the command *piped* into `tee`, not necessarily the exit code that caused the `if` condition to fail. This might not capture the true failure code.

### contain
- ISSUE: Silenced errors in mkdir - problem: Critical directory creation failures are hidden, preventing diagnosis.
- ISSUE: Silenced errors in tee (memory.limit_in_bytes) - problem: Writes to cgroup files can fail silently, leading to incorrect resource limits.
- ISSUE: Silenced errors in tee (memory.swappiness) - problem: Configuration failure is ignored due to `|| true`, potentially leaving default swappiness.
- ISSUE: Silenced errors in nproc - problem: Failure to determine core count is hidden before fallback, obscuring issues.
- ISSUE: cpu.cfs_period_us and cpu.cfs_quota_us writes use `|| true` - problem: Failures to set CPU quota are ignored, so the CPU limits might not be applied.
- ISSUE: Ollama PID pgrep uses `2>/dev/null` - problem: Failure to find Ollama PID is silent, obscuring startup or configuration issues.
- ISSUE: Ollama PID writes to cgroup.procs use `|| true` - problem: Failure to move Ollama process into cgroup is ignored, meaning Ollama might not be contained.
- ISSUE: Larql PID writes to cgroup.procs use `|| true` - problem: Failures to move Larql process into cgroup are ignored, meaning Larql might not be contained.
- ISSUE: Larql function returns silently if cgroup is not set up - problem: Inconsistency in error handling and user feedback when cgroup setup is a prerequisite.
- ISSUE: `CGROUP_ROOT` is not validated - problem: If `CGROUP_ROOT` is incorrect, multiple subsequent operations will fail and be silently ignored.
- ISSUE: CPU core calculation is simplistic - problem: `CGROUP_CPU_RESERVE_CORES` logic can result in a minimum of 1 core being allocated even if fewer are available, which might be unexpected.
- ISSUE: Mix of `return 1` and `|| true` for error handling - problem: Inconsistent error handling across critical operations; some failures halt execution, others are ignored.

## Confirmed by code-reading / reproduction [read]
- **[read] pi-harness word-drop** — unquoted multi-word TASK truncates to the last word (`bin/pi-harness:201`, `*) TASK="$1"`). Reproduced: `write a hello function` -> `function`.
- **[read] coding-agent false-positive error detection** — `_run_goose_call` greps output for rate-limit sentinels, so reading any file containing those strings is misclassified as failure (`bin/coding-agent:215-231`).
- **[read] opposite fallbacks** — pi-harness falls back to Ollama; coding-agent to larql. The general runner uses the unusable path.
- **[read] `ollama/...` misroute** — coding-agent `--model ollama/x` silently runs on OpenRouter (`bin/coding-agent:336-357`).
- **[read] output-contract split** — JSONL (pi) vs unstructured Goose text; forces two parse paths.
- **[read] containment posture divergence** — mandatory cgroup (pi-harness) vs best-effort (coding-agent).

_Resolution: all of the above are addressed by the unified `bin/babel` dispatcher (ADR-0001)._

## Filed issues (metavacua/babel-harness)
- **#18** — pi-harness arg parsing: word-drop + no `--model` validation + no `--` support
- **#19** — coding-agent `_run_goose_call` false-positive rate-limit detection
- **#20** — coding-agent `--model ollama/…` silently runs on OpenRouter
- **#21** — fragmentation → unify under `bin/babel` (ADR-0001) [tracking]
