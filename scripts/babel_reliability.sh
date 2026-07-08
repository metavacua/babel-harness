#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# babel_reliability.sh — quantify the input→output reliability of the babel
# subagent on the substrate we actually have (free/cheap models). It runs one
# prompt N times through pi-harness, grades each reply against a deterministic
# oracle regex, and reports success-rate, rate-limit-rate, and reply-length
# variance. This is the measurement instrument for "make the cheapest thing
# perform as well as the expensive thing": compare regimes by their numbers.
#
# Usage:
#   babel_reliability.sh --n 5 --label grounded --oracle 'SUPPORTED' -- "PROMPT..."
# Env:
#   OPENROUTER_MODEL (default: pi-harness 'auto' selection)
#   TRIAL_SLEEP (seconds between trials; default 3, to ease free-tier rate limits)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PI="${PI_HARNESS:-$REPO_ROOT/bin/pi-harness}"
N=5; LABEL="run"; ORACLE="."; TRIAL_SLEEP="${TRIAL_SLEEP:-3}"
while [[ $# -gt 0 ]]; do case "$1" in
  --n) N="$2"; shift 2;; --label) LABEL="$2"; shift 2;;
  --oracle) ORACLE="$2"; shift 2;; --) shift; break;;
  *) break;; esac; done
PROMPT="$*"
[ -z "$PROMPT" ] && { echo "usage: babel_reliability.sh --n N --oracle REGEX -- PROMPT" >&2; exit 2; }

_reply_from() {  # jsonl-file -> final assistant text
  python3 - "$1" <<'PY'
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
print(txt)
PY
}

pass=0; ratelimit=0; fail=0; totlen=0; sumsq=0; done=0
echo "== reliability: label=$LABEL n=$N oracle=/$ORACLE/ model=${OPENROUTER_MODEL:-auto} =="
for i in $(seq 1 "$N"); do
  out="$(mktemp)"
  OPENROUTER_MODEL="${OPENROUTER_MODEL:-auto}" timeout 120 "$PI" "$PROMPT" >"$out" 2>"$out.err" || true
  reply="$(_reply_from "$out")"
  errtxt="$(cat "$out.err" 2>/dev/null)"
  rm -f "$out" "$out.err"
  if echo "$reply $errtxt" | grep -qiE 'rate.?limit|Ran into this error|Provider returned error|no provider available'; then
    ratelimit=$((ratelimit+1)); verdict="RATE_LIMIT"
  elif echo "$reply" | grep -qE "$ORACLE"; then
    pass=$((pass+1)); done=$((done+1)); verdict="PASS"
  else
    fail=$((fail+1)); done=$((done+1)); verdict="FAIL"
  fi
  len=${#reply}
  [ "$verdict" != "RATE_LIMIT" ] && { totlen=$((totlen+len)); sumsq=$((sumsq+len*len)); }
  printf '  trial %2d: %-10s len=%-5d %s\n' "$i" "$verdict" "$len" "$(echo "$reply" | head -c 60 | tr '\n' ' ')"
  sleep "$TRIAL_SLEEP"
done

echo "  ----"
graded=$((pass+fail))
rate="n/a"; [ "$graded" -gt 0 ] && rate="$(awk "BEGIN{printf \"%.0f%%\", 100*$pass/$graded}")"
meanlen="n/a"; [ "$graded" -gt 0 ] && meanlen="$(awk "BEGIN{printf \"%.0f\", $totlen/$graded}")"
echo "  PASS=$pass FAIL=$fail RATE_LIMIT=$ratelimit  | success-rate(graded)=$rate  mean-reply-len=$meanlen"
