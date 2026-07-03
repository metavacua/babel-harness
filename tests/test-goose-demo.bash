#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Mocks-only test suite for scripts/run_goose_demo.sh. No live model/shim
# is ever started here -- a tiny inline python HTTP server stands in for
# scripts/pipeline/chat_shim.py (wired in via the CHAT_SHIM_CMD seam) and a
# tiny inline bash script stands in for goose (wired in via GOOSE_BIN, same
# seam run_agent_battery.sh already exposes; pattern mirrors
# tests/mocks/goose but this mock actually calls the shim and relays its
# answer, rather than returning a fixed canned string, so patched-vs
# -ablation divergence is observable in the transcript).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$REPO_ROOT/scripts/run_goose_demo.sh"
PASS=0; FAIL=0

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qFe "$needle"; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc (wanted: $needle)"; ((FAIL++)) || true
  fi
}

assert_exit() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc (exit $actual)"; ((PASS++)) || true
  else
    echo "  FAIL: $desc (expected exit $expected, got $actual)"; ((FAIL++)) || true
  fi
}

assert_true() {
  local desc="$1" cond="$2"
  if [ "$cond" = "1" ]; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc"; ((FAIL++)) || true
  fi
}

# -- shared mock fixtures ---------------------------------------------------
MOCKDIR=$(mktemp -d)
trap 'rm -rf "$MOCKDIR"' EXIT

MOCK_SHIM="$MOCKDIR/mock_chat_shim.py"
cat > "$MOCK_SHIM" <<'PYEOF'
#!/usr/bin/env python3
# Test-only stand-in for scripts/pipeline/chat_shim.py: same CLI surface
# (--bin/--vindex/--port/--patches-dir/--no-patches) so run_goose_demo.sh's
# CHAT_SHIM_CMD invocation is unchanged; behaviour is controlled by env
# vars instead of a real driver/vindex.
#
# Env seams:
#   MOCK_SHIM_CRASH=1        -- exit(1) immediately, before binding a socket
#                                (simulates a shim that dies on launch)
#   MOCK_SHIM_MARKER_DIR     -- if set, write "$dir/$pid" on start and
#                                remove it on clean SIGTERM/SIGINT shutdown
#                                (proves the caller's explicit-PID kill
#                                actually reaped THIS process)
#   MOCK_SHIM_TARGET         -- the expected-answer token (default "TARGET")
#   MOCK_SHIM_ECHO_ABLATION  -- "1" => also echo the target when launched
#                                with --no-patches (models the "confounded"
#                                control: both patched and ablation hit)
import argparse
import http.server
import json
import os
import signal
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin")
    ap.add_argument("--vindex")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--patches-dir")
    ap.add_argument("--no-patches", action="store_true")
    args = ap.parse_args()

    if os.environ.get("MOCK_SHIM_CRASH") == "1":
        print("mock_chat_shim: MOCK_SHIM_CRASH=1, exiting before bind",
              file=sys.stderr)
        sys.exit(1)

    marker_dir = os.environ.get("MOCK_SHIM_MARKER_DIR")
    marker_path = None
    if marker_dir:
        marker_path = os.path.join(marker_dir, str(os.getpid()))
        with open(marker_path, "w") as fh:
            fh.write(str(os.getpid()))

    def _remove_marker():
        if marker_path and os.path.exists(marker_path):
            os.remove(marker_path)

    def _cleanup(_signum, _frame):
        _remove_marker()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    patched = not args.no_patches
    echo_ablation = os.environ.get("MOCK_SHIM_ECHO_ABLATION") == "1"
    target = os.environ.get("MOCK_SHIM_TARGET", "TARGET")
    should_echo = patched or echo_ablation
    content = target if should_echo else "unknown-no-match"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            if self.path == "/v1/models":
                body = b'{"object":"list","data":[{"id":"mock"}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            self.rfile.read(length)
            payload = {
                "id": "mock-chatcmpl",
                "object": "chat.completion",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }],
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    try:
        httpd.serve_forever()
    finally:
        _remove_marker()


if __name__ == "__main__":
    main()
PYEOF
chmod +x "$MOCK_SHIM"

MOCK_GOOSE="$MOCKDIR/mock_goose"
cat > "$MOCK_GOOSE" <<'BASHEOF'
#!/usr/bin/env bash
set -uo pipefail
# Test-only stand-in for goose (pattern per tests/mocks/goose): calls the
# shim's /v1/chat/completions (via OPENAI_BASE_URL, same env wiring
# run_agent_battery.sh already exports) and prints whatever content the
# shim returned -- so patched-vs-ablation divergence in the SHIM's answer
# is what ends up in the transcript, exactly like a real goose relaying a
# real model's reply.
resp=$(curl -sf -X POST "${OPENAI_BASE_URL}/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"mock","messages":[{"role":"user","content":"question"}]}')
rc=$?
if [ $rc -ne 0 ]; then
  echo "mock_goose: request to shim failed (rc=$rc)" >&2
  exit 1
fi
echo "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"])'
BASHEOF
chmod +x "$MOCK_GOOSE"

echo "=== goose-demo test suite ==="

echo ""
echo "--- 1: port-busy pre-check fails loudly ---"
tmpdir=$(mktemp -d)
port=18401
holder_pid=""
python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1 &
holder_pid=$!
for _ in $(seq 1 20); do
  ss -tlnp 2>/dev/null | grep -qE ":${port}([[:space:]]|\$)" && break
  sleep 0.2
done
printf 'q1\n' > "$tmpdir/questions.txt"
printf 'exp1\n' > "$tmpdir/expected.txt"
out=$(CHAT_SHIM_CMD="python3 -u $MOCK_SHIM" \
      bash "$RUNNER" --bin /bin/true --vindex /bin/true \
      --patches-dir "$tmpdir/patches" \
      --questions "$tmpdir/questions.txt" --expected "$tmpdir/expected.txt" \
      --out "$tmpdir/out" --port "$port" 2>&1)
rc=$?
kill "$holder_pid" 2>/dev/null || true
wait "$holder_pid" 2>/dev/null || true
assert_exit "exits 1 when port busy" "1" "$rc"
assert_contains "loud port-busy message" "port ${port} busy — refusing to start (stale shim?)" "$out"
rm -rf "$tmpdir"

echo ""
echo "--- 2: health-gate fails loudly when shim exits immediately ---"
tmpdir=$(mktemp -d)
port=18402
printf 'q1\n' > "$tmpdir/questions.txt"
printf 'exp1\n' > "$tmpdir/expected.txt"
t0=$(date +%s)
out=$(CHAT_SHIM_CMD="python3 -u $MOCK_SHIM" MOCK_SHIM_CRASH=1 \
      SHIM_HEALTH_TIMEOUT_S=5 \
      bash "$RUNNER" --bin /bin/true --vindex /bin/true \
      --patches-dir "$tmpdir/patches" \
      --questions "$tmpdir/questions.txt" --expected "$tmpdir/expected.txt" \
      --out "$tmpdir/out" --port "$port" 2>&1)
rc=$?
t1=$(date +%s)
assert_exit "exits 1 when shim exits immediately" "1" "$rc"
assert_contains "reports shim exited before healthy" "exited before becoming healthy" "$out"
assert_contains "includes shim log tail marker" "shim log tail" "$out"
elapsed=$((t1 - t0))
if [ "$elapsed" -lt 15 ]; then
  echo "  PASS: health-gate failed fast (${elapsed}s), did not wait out full timeout"; ((PASS++)) || true
else
  echo "  FAIL: health-gate took ${elapsed}s -- should fail fast on a dead pid"; ((FAIL++)) || true
fi
rm -rf "$tmpdir"

echo ""
echo "--- 3: happy path -- patched hits, ablation misses -> agent-vindex ---"
tmpdir=$(mktemp -d)
port=18403
marker_dir="$tmpdir/markers"
mkdir -p "$marker_dir"
printf 'What is the capital of Freedonia?\n' > "$tmpdir/questions.txt"
printf 'Duckopolis\n' > "$tmpdir/expected.txt"
out=$(CHAT_SHIM_CMD="python3 -u $MOCK_SHIM" GOOSE_BIN="$MOCK_GOOSE" \
      MOCK_SHIM_TARGET="Duckopolis" MOCK_SHIM_MARKER_DIR="$marker_dir" \
      bash "$RUNNER" --bin /bin/true --vindex /bin/true \
      --patches-dir "$tmpdir/patches" \
      --questions "$tmpdir/questions.txt" --expected "$tmpdir/expected.txt" \
      --out "$tmpdir/out" --port "$port" 2>&1)
rc=$?
assert_exit "exits 0 on a completed happy-path run" "0" "$rc"
assert_contains "reports 1/1 agent-vindex-attributed" "1/1 agent-vindex-attributed" "$out"
if [ -f "$tmpdir/out/goose-demo-summary.md" ]; then
  summary=$(cat "$tmpdir/out/goose-demo-summary.md")
  assert_contains "summary table marks q1 PASS (agent-vindex)" "PASS (agent-vindex)" "$summary"
else
  echo "  FAIL: goose-demo-summary.md not written"; ((FAIL++)) || true
fi
if [ -f "$tmpdir/out/goose-demo-results.jsonl" ]; then
  results=$(cat "$tmpdir/out/goose-demo-results.jsonl")
  assert_contains "results.jsonl records agent-vindex attribution" '"attribution":"agent-vindex"' "$results"
  assert_contains "results.jsonl records verdict PASS" '"verdict":"PASS"' "$results"
else
  echo "  FAIL: goose-demo-results.jsonl not written"; ((FAIL++)) || true
fi
# assertion 5: both shims (patched pass + ablation pass) were stopped by
# their explicit PID -- the marker dir must be empty at the end.
leftover=$(ls -A "$marker_dir" 2>/dev/null | wc -l)
assert_true "no leftover mock shim process (marker dir empty)" "$([ "$leftover" -eq 0 ] && echo 1 || echo 0)"
rm -rf "$tmpdir"

echo ""
echo "--- 4: confounded -- both patched and ablation hit -> FAIL ---"
tmpdir=$(mktemp -d)
port=18404
printf 'What is the capital of Freedonia?\n' > "$tmpdir/questions.txt"
printf 'Duckopolis\n' > "$tmpdir/expected.txt"
out=$(CHAT_SHIM_CMD="python3 -u $MOCK_SHIM" GOOSE_BIN="$MOCK_GOOSE" \
      MOCK_SHIM_TARGET="Duckopolis" MOCK_SHIM_ECHO_ABLATION=1 \
      bash "$RUNNER" --bin /bin/true --vindex /bin/true \
      --patches-dir "$tmpdir/patches" \
      --questions "$tmpdir/questions.txt" --expected "$tmpdir/expected.txt" \
      --out "$tmpdir/out" --port "$port" 2>&1)
rc=$?
assert_exit "exits 0 even though the question is confounded" "0" "$rc"
assert_contains "reports 0/1 agent-vindex-attributed" "0/1 agent-vindex-attributed" "$out"
if [ -f "$tmpdir/out/goose-demo-results.jsonl" ]; then
  results=$(cat "$tmpdir/out/goose-demo-results.jsonl")
  assert_contains "results.jsonl records confounded attribution" '"attribution":"confounded"' "$results"
  assert_contains "results.jsonl records verdict FAIL" '"verdict":"FAIL"' "$results"
else
  echo "  FAIL: goose-demo-results.jsonl not written"; ((FAIL++)) || true
fi
rm -rf "$tmpdir"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
