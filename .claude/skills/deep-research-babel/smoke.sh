#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Smoke test for the deep-research-babel skill.
#
# Its PRIMARY job is anti-regression: assert that babel is wired into the skill
# as an EXECUTABLE invocation, not merely mentioned as prose / a repo name / a
# comment. This is the structural guard against a future edit quietly reducing
# babel to an inert token (the exact failure this skill was created to reverse).
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_MD="$SKILL_DIR/SKILL.md"
export PATH="$HOME/babel-harness/bin:$PATH"

pass=0; fail=0
ok()   { printf '  PASS  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

echo "== deep-research-babel smoke =="

# --- Static invariant: babel must be EXECUTABLE, not just mentioned ----------
# Strip fenced-code detection down to real invocations: a line that runs
# pi-harness or coding-agent as a command (not the word inside prose).
exec_calls=$(grep -E '(^|[^-a-z])(pi-harness|coding-agent)[[:space:]]+("|--|[A-Za-z])' "$SKILL_MD" | wc -l | tr -d ' ')
if [ "$exec_calls" -ge 3 ]; then
  ok "SKILL.md has $exec_calls executable babel invocations (>=3 required)"
else
  bad "SKILL.md has only $exec_calls executable babel invocations (need >=3) — babel may have been downgraded to a mention"
fi

# The three cognition phases (extract / verify / synthesize) must each route to babel.
for phase in "Extract claims (BABEL)" "verify (BABEL" "Synthesize (BABEL"; do
  if grep -qF "$phase" "$SKILL_MD"; then ok "cognition phase present: $phase"
  else bad "missing babel cognition phase: $phase"; fi
done

# The grounding invariant must be stated: babel reasons over provided text, never invents sources.
if grep -qiE 'never (asked to \*?find\*?|invent)|only ever sees|only reason over text' "$SKILL_MD"; then
  ok "grounding invariant present (babel never invents sources)"
else
  bad "grounding invariant missing — fabricated-citation guard not documented"
fi

# --- Live invariant (only if a provider is reachable): grounded round-trip ---
# NB: capture to a var first — piping into `grep -q` under `set -o pipefail`
# makes grep close the pipe on first match, pi-harness takes SIGPIPE (141), and
# pipefail reports the pipeline as failed → the live check would silently skip.
status_out="$(pi-harness --status 2>/dev/null || true)"
if printf '%s' "$status_out" | grep -qiE 'reachable|running'; then
  SRC="The Eiffel Tower is 330 metres tall and was completed in 1889."
  CLAIM="The Eiffel Tower is 330 metres tall."
  OUT=/tmp/drb-live-$$.jsonl
  if timeout 90 pi-harness "You are a skeptic. Does the SOURCE support the CLAIM verbatim? \
Answer SUPPORTED or UNSUPPORTED and quote the decisive sentence. \
CLAIM: $CLAIM  SOURCE: $SRC" > "$OUT" 2>/dev/null; then
    reply=$(python3 - "$OUT" <<'PY'
import sys, json, pathlib
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    try: obj = json.loads(line)
    except Exception: continue
    if obj.get('type') == 'agent_end':
        for m in reversed(obj.get('messages', [])):
            for b in m.get('content', []):
                if b.get('type') == 'text':
                    print(b['text']); sys.exit(0)
PY
)
    if echo "$reply" | grep -qi "SUPPORTED"; then
      ok "live grounded round-trip: babel judged the grounded claim (reply len ${#reply})"
    else
      bad "live round-trip returned no SUPPORTED/UNSUPPORTED verdict (grounding weak): ${reply:0:120}"
    fi
    rm -f "$OUT"
  else
    bad "live pi-harness call failed/timed out"
  fi
else
  echo "  SKIP  live round-trip (no provider reachable)"
fi

echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
