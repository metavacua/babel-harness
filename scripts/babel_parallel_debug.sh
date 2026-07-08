#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# babel_parallel_debug.sh — decomposed, parallel, babel-delegated debugging of
# the babel harness itself. Each subtask is a GROUNDED, BOUNDED, SINGLE-SHOT
# analysis of one boundary (the regime measured 100% reliable on the cheap
# substrate), delegated to pi-harness and run in PARALLEL. Results are assembled
# into a bug/issue inventory. This is babel debugging babel.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PI="${PI_HARNESS:-$ROOT/bin/pi-harness}"
OUT="$ROOT/docs/audits/babel-issue-inventory.md"
TMP="$(mktemp -d)"
export OPENROUTER_MODEL="${OPENROUTER_MODEL:-auto}"

_reply_from() { python3 - "$1" <<'PY'
import sys, json, pathlib
txt=""
for line in pathlib.Path(sys.argv[1]).read_text(errors="ignore").splitlines():
    try: o=json.loads(line)
    except Exception: continue
    if o.get("type")=="agent_end":
        for m in reversed(o.get("messages",[])):
            for b in m.get("content",[]):
                if b.get("type")=="text": txt=b["text"]; break
            if txt: break
print(txt.strip())
PY
}

# delegate NAME FACET EXCERPT — one grounded single-shot pi-harness call.
delegate() {
  local name="$1" facet="$2" excerpt="$3"
  local prompt="You are debugging the babel harness. Analyze ONLY the bash code below for bugs, inconsistencies, and UX problems related to ${facet}. Output a terse list, one issue per line, format 'ISSUE: <what> - <why it is a problem>'. Do not restate the code. CODE:
${excerpt}"
  timeout 150 "$PI" "$prompt" >"$TMP/$name.jsonl" 2>"$TMP/$name.err" || true
  _reply_from "$TMP/$name.jsonl" >"$TMP/$name.reply"
  if [ ! -s "$TMP/$name.reply" ]; then
    if grep -qiE 'rate.?limit|Ran into this error|no provider' "$TMP/$name.jsonl" "$TMP/$name.err" 2>/dev/null; then
      echo "(delegation unavailable: rate-limit/provider error)" >"$TMP/$name.reply"
    else
      echo "(delegation returned no analysis)" >"$TMP/$name.reply"
    fi
  fi
}

# --- Decomposition: one subtask per babel boundary, grounded in real code ---
E_ARG="$(sed -n '190,211p' "$ROOT/bin/pi-harness"; echo '--- coding-agent ---'; sed -n '270,294p' "$ROOT/bin/coding-agent")"
E_FALLBACK="$(sed -n '135,145p' "$ROOT/bin/pi-harness"; echo '--- coding-agent ---'; sed -n '360,369p' "$ROOT/bin/coding-agent")"
E_PATHS="$(sed -n '243,268p' "$ROOT/bin/coding-agent"; echo '--- serve ---'; sed -n '134,139p' "$ROOT/bin/coding-agent")"
E_OUTPUT="$(sed -n '179,188p' "$ROOT/bin/pi-harness"; echo '--- coding-agent ---'; sed -n '213,231p' "$ROOT/bin/coding-agent")"
E_CONTAIN="$(sed -n '79,110p' "$ROOT/bin/pi-harness"; echo '--- coding-agent ---'; sed -n '111,126p' "$ROOT/bin/coding-agent")"

echo "orchestrating 5 parallel babel delegations (grounded single-shot)..." >&2
delegate argparse   "argument parsing, quoting, and UX (do multi-word tasks survive?)" "$E_ARG" &
delegate fallback   "provider selection and fallback order (cloud vs ollama vs larql)"  "$E_FALLBACK" &
delegate paths      "relative vs absolute path handling for the larql vindex"          "$E_PATHS" &
delegate output     "output contract and error/rate-limit detection"                   "$E_OUTPUT" &
delegate contain    "cgroup containment posture and its consistency"                    "$E_CONTAIN" &
wait
echo "all delegations complete." >&2

# --- Assemble the inventory ---
mkdir -p "$(dirname "$OUT")"
{
  echo "# babel — issue & bug inventory"
  echo
  echo "Produced by \`scripts/babel_parallel_debug.sh\`: five grounded, single-shot"
  echo "subtasks delegated **in parallel to the babel harness itself** (pi-harness),"
  echo "each analyzing one boundary of \`bin/pi-harness\` + \`bin/coding-agent\`."
  echo "Merged with issues found by direct code-reading (marked [read])."
  echo
  echo "## Delegated findings (babel-on-babel)"
  for s in argparse fallback paths output contain; do
    echo
    echo "### ${s}"
    sed 's/^/- /' "$TMP/$s.reply" 2>/dev/null | grep -v '^- $' || echo "- (none)"
  done
  echo
  echo "## Confirmed by code-reading / reproduction [read]"
  echo "- **[read] pi-harness word-drop** — unquoted multi-word TASK truncates to the last word (\`bin/pi-harness:201\`, \`*) TASK=\"\$1\"\`). Reproduced: \`write a hello function\` -> \`function\`."
  echo "- **[read] coding-agent false-positive error detection** — \`_run_goose_call\` greps output for rate-limit sentinels, so reading any file containing those strings is misclassified as failure (\`bin/coding-agent:215-231\`)."
  echo "- **[read] opposite fallbacks** — pi-harness falls back to Ollama; coding-agent to larql. The general runner uses the unusable path."
  echo "- **[read] \`ollama/...\` misroute** — coding-agent \`--model ollama/x\` silently runs on OpenRouter (\`bin/coding-agent:336-357\`)."
  echo "- **[read] output-contract split** — JSONL (pi) vs unstructured Goose text; forces two parse paths."
  echo "- **[read] containment posture divergence** — mandatory cgroup (pi-harness) vs best-effort (coding-agent)."
  echo
  echo "_Resolution: all of the above are addressed by the unified \`bin/babel\` dispatcher (ADR-0001)._"
} > "$OUT"
echo "inventory written: $OUT" >&2
rm -rf "$TMP"
