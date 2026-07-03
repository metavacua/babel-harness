#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Mocks-only test suite for bin/vindex-serve. No live model, larql serve, or
# chat_shim.py is ever started here -- a tiny inline python HTTP server
# stands in for the real backend, wired in via the VINDEX_SERVE_BACKEND_CMD
# seam (mirrors tests/test-goose-demo.bash's CHAT_SHIM_CMD seam).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BS="$REPO_ROOT/bin/vindex-serve"
PASS=0; FAIL=0

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF -- "$needle"; then
    echo "  PASS: $desc"; ((PASS++)) || true
  else
    echo "  FAIL: $desc"
    echo "    expected to contain: $needle"
    echo "    actual:   $haystack"
    ((FAIL++)) || true
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

# -- shared mock fixture -----------------------------------------------------
MOCKDIR=$(mktemp -d)
trap 'rm -rf "$MOCKDIR"' EXIT

MOCK_SERVER="$MOCKDIR/mock_server.py"
cat > "$MOCK_SERVER" <<'PYEOF'
#!/usr/bin/env python3
# Test-only stand-in for `larql serve`/chat_shim.py: serves a bare
# /v1/models 200 so vindex-serve's health-poll can pass, without loading
# any model. Same --port surface the real backends expose.
#
# Env seams:
#   MOCK_SERVER_CRASH=1  -- exit(1) immediately, before binding a socket
#                            (simulates a backend that dies on launch)
import argparse
import http.server
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
args = ap.parse_args()

if os.environ.get("MOCK_SERVER_CRASH") == "1":
    print("mock_server: MOCK_SERVER_CRASH=1, exiting before bind",
          file=sys.stderr)
    sys.exit(1)


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


httpd = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
httpd.serve_forever()
PYEOF
chmod +x "$MOCK_SERVER"

# Test-only stand-in for the real "larql-probe safe (sudo systemd-run
# --scope) -- larql serve" chain, WITHOUT needing real sudo/systemd (which
# would break this suite's mocks-only, no-privilege contract). Reproduces
# the exact hazard bin/vindex-serve's header comment documents under "KNOWN
# LIMITATION" -- confirmed empirically during this script's own development
# by wrapping `sleep` in the real `larql-probe safe`: killing the tracked
# `$!` pid (the wrapper) does NOT kill the payload, because systemd-run
# --scope never execs into it. Here, os.fork() stands in for that same
# "tracked pid != actual payload pid" shape: the parent is what vindex-serve
# tracks and kills; the child inherits the listening socket and keeps
# answering /v1/models on its own, independent of the parent's fate.
MOCK_FORK_SERVER="$MOCKDIR/mock_fork_server.py"
cat > "$MOCK_FORK_SERVER" <<'PYEOF'
#!/usr/bin/env python3
# Env seams:
#   MOCK_FORK_MARKER_DIR -- required; child writes its own pid to
#                           "$dir/child_pid" so the test can reap it
#                           afterward (vindex-serve, correctly, cannot).
import argparse
import http.server
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
args = ap.parse_args()

marker_dir = os.environ["MOCK_FORK_MARKER_DIR"]


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


httpd = http.server.HTTPServer(("127.0.0.1", args.port), Handler)

pid = os.fork()
if pid == 0:
    # Child: inherits the bound listening socket and keeps serving on it,
    # completely independent of whatever happens to the parent.
    httpd.serve_forever()
else:
    with open(os.path.join(marker_dir, "child_pid"), "w") as fh:
        fh.write(str(pid))
    sys.stderr.write(f"mock_fork_server: parent {os.getpid()} forked child {pid}\n")
    # Parent: does nothing but idle. Its own copy of the listening fd is
    # never accept()-ed on, so all real traffic goes to the child.
    while True:
        time.sleep(3600)
PYEOF
chmod +x "$MOCK_FORK_SERVER"

export VINDEX_SERVE_BACKEND_CMD="python3 -u $MOCK_SERVER"

# per-test isolated pid/log dirs so runs never collide with a real
# ~/.cache/babel-harness in case this suite runs on a dev box
WORKDIR=$(mktemp -d)
export VINDEX_SERVE_PIDDIR="$WORKDIR/piddir"
export VINDEX_SERVE_LOGDIR="$WORKDIR/logdir"

pidfile_for() { echo "$VINDEX_SERVE_PIDDIR/vindex-serve-$1.pid"; }

port_free() {
  ! ss -tlnp 2>/dev/null | grep -qE ":${1}([[:space:]]|\$)"
}

wait_port_free() {
  local port="$1" waited=0
  while [ "$waited" -lt 10 ]; do
    port_free "$port" && return 0
    sleep 0.5
    waited=$((waited + 1))
  done
  return 1
}

echo "=== vindex-serve test suite ==="

echo ""
echo "--- 1: start writes pidfile + health-passes + status=running(0) ---"
PORT=18601
out=$(bash "$BS" start --vindex /tmp/fake-1.vindex --port "$PORT" --backend shim 2>&1)
rc=$?
assert_exit "start exits 0" "0" "$rc"
assert_contains "start reports ready" "ready (pid" "$out"
assert_true "pidfile written" "$([ -f "$(pidfile_for "$PORT")" ] && echo 1 || echo 0)"
sout=$(bash "$BS" status --port "$PORT" 2>&1)
src=$?
assert_exit "status exits 0 (running)" "0" "$src"
assert_contains "status reports running" "running (pid" "$sout"
bash "$BS" stop --port "$PORT" >/dev/null 2>&1
wait_port_free "$PORT"

echo ""
echo "--- 2: start again is idempotent, no second process ---"
PORT=18602
bash "$BS" start --vindex /tmp/fake-2.vindex --port "$PORT" --backend shim >/dev/null 2>&1
pid1=$(cut -f1 "$(pidfile_for "$PORT")")
out=$(bash "$BS" start --vindex /tmp/fake-2.vindex --port "$PORT" --backend shim 2>&1)
rc=$?
pid2=$(cut -f1 "$(pidfile_for "$PORT")")
assert_exit "second start exits 0" "0" "$rc"
assert_contains "second start reports already running" "already running" "$out"
assert_true "same pid, no second process launched" "$([ "$pid1" = "$pid2" ] && echo 1 || echo 0)"
nprocs=$(pgrep -f -- "--port $PORT" 2>/dev/null | wc -l)
assert_true "exactly one backend process for the port" "$([ "$nprocs" -eq 1 ] && echo 1 || echo 0)"
bash "$BS" stop --port "$PORT" >/dev/null 2>&1
wait_port_free "$PORT"

echo ""
echo "--- 3: stop kills the exact pidfile pid, removes pidfile, frees port ---"
PORT=18603
bash "$BS" start --vindex /tmp/fake-3.vindex --port "$PORT" --backend shim >/dev/null 2>&1
pid=$(cut -f1 "$(pidfile_for "$PORT")")
out=$(bash "$BS" stop --port "$PORT" 2>&1)
rc=$?
assert_exit "stop exits 0" "0" "$rc"
assert_contains "stop reports the exact pid" "stopped (pid $pid)" "$out"
assert_true "process is gone" "$(kill -0 "$pid" 2>/dev/null && echo 0 || echo 1)"
assert_true "pidfile removed" "$([ ! -f "$(pidfile_for "$PORT")" ] && echo 1 || echo 0)"
wait_port_free "$PORT"
assert_true "port released" "$(port_free "$PORT" && echo 1 || echo 0)"
sout=$(bash "$BS" status --port "$PORT" 2>&1)
src=$?
assert_exit "status exits 3 (not running)" "3" "$src"
assert_contains "status reports not running" "not running" "$sout"

echo ""
echo "--- 3b: stop is idempotent when nothing is running ---"
out=$(bash "$BS" stop --port "$PORT" 2>&1)
rc=$?
assert_exit "stop on absent pidfile exits 0" "0" "$rc"
assert_contains "stop reports not running" "not running" "$out"

echo ""
echo "--- 4: foreign port holder -> start fails loudly, exit 1 ---"
PORT=18604
python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
holder_pid=$!
for _ in $(seq 1 20); do
  ss -tlnp 2>/dev/null | grep -qE ":${PORT}([[:space:]]|\$)" && break
  sleep 0.2
done
out=$(bash "$BS" start --vindex /tmp/fake-4.vindex --port "$PORT" --backend shim 2>&1)
rc=$?
kill "$holder_pid" 2>/dev/null || true
wait "$holder_pid" 2>/dev/null || true
assert_exit "start exits 1 on foreign port holder" "1" "$rc"
assert_contains "loud foreign-pid refusal message" "held by foreign pid $holder_pid — refusing" "$out"
assert_true "no pidfile left behind" "$([ ! -f "$(pidfile_for "$PORT")" ] && echo 1 || echo 0)"

echo ""
echo "--- 5: backend that exits immediately -> start fails, log tail, pidfile cleaned ---"
PORT=18605
out=$(MOCK_SERVER_CRASH=1 LARQL_START_TIMEOUT=5 \
      bash "$BS" start --vindex /tmp/fake-5.vindex --port "$PORT" --backend shim 2>&1)
rc=$?
assert_exit "start exits 1 when backend exits immediately" "1" "$rc"
assert_contains "reports backend exited before healthy" "exited before becoming healthy" "$out"
assert_contains "includes backend log tail" "backend log tail" "$out"
assert_contains "log tail shows the crash reason" "MOCK_SERVER_CRASH=1" "$out"
assert_true "pidfile cleaned up" "$([ ! -f "$(pidfile_for "$PORT")" ] && echo 1 || echo 0)"

echo ""
echo "--- 6: restart works ---"
PORT=18606
bash "$BS" start --vindex /tmp/fake-6.vindex --port "$PORT" --backend shim >/dev/null 2>&1
pid_before=$(cut -f1 "$(pidfile_for "$PORT")")
out=$(bash "$BS" restart --vindex /tmp/fake-6.vindex --port "$PORT" --backend shim 2>&1)
rc=$?
pid_after=$(cut -f1 "$(pidfile_for "$PORT")" 2>/dev/null)
assert_exit "restart exits 0" "0" "$rc"
assert_contains "restart reports ready" "ready (pid" "$out"
assert_true "restart produced a new pid" "$([ -n "$pid_after" ] && [ "$pid_before" != "$pid_after" ] && echo 1 || echo 0)"
assert_true "old pid is gone after restart" "$(kill -0 "$pid_before" 2>/dev/null && echo 0 || echo 1)"
sout=$(bash "$BS" status --port "$PORT" 2>&1)
assert_contains "status running after restart" "running (pid $pid_after" "$sout"
bash "$BS" stop --port "$PORT" >/dev/null 2>&1
wait_port_free "$PORT"

echo ""
echo "--- 6b: orphaned-payload regression -- stop fails loudly when the tracked pid dies but the port stays held (mocks larql-probe's confirmed sudo systemd-run --scope orphan hazard, without needing real sudo/systemd) ---"
PORT=18607
FORK_MARKERS=$(mktemp -d)
out=$(VINDEX_SERVE_BACKEND_CMD="python3 -u $MOCK_FORK_SERVER" \
      MOCK_FORK_MARKER_DIR="$FORK_MARKERS" \
      bash "$BS" start --vindex /tmp/fake-6b.vindex --port "$PORT" --backend shim 2>&1)
rc=$?
assert_exit "start (forking mock) exits 0" "0" "$rc"
tracked_pid=$(cut -f1 "$(pidfile_for "$PORT")")
# give the mock a moment to fork and write its marker
for _ in $(seq 1 20); do [ -s "$FORK_MARKERS/child_pid" ] && break; sleep 0.2; done
child_pid=$(cat "$FORK_MARKERS/child_pid" 2>/dev/null || echo "")
assert_true "child pid was recorded" "$([ -n "$child_pid" ] && echo 1 || echo 0)"

out=$(bash "$BS" stop --port "$PORT" 2>&1)
rc=$?
assert_exit "stop exits 1 -- tracked pid died but port is still held" "1" "$rc"
assert_contains "warns the port is still held" "still held" "$out"
assert_true "tracked (parent) pid is actually gone" "$(kill -0 "$tracked_pid" 2>/dev/null && echo 0 || echo 1)"
assert_true "orphaned child is STILL running (the regression this guards)" "$(kill -0 "$child_pid" 2>/dev/null && echo 1 || echo 0)"
assert_true "pidfile is still removed despite the warning" "$([ ! -f "$(pidfile_for "$PORT")" ] && echo 1 || echo 0)"

# Cleanup: only the test can reap this orphan -- that is the whole point.
kill -9 "$child_pid" 2>/dev/null || true
wait "$child_pid" 2>/dev/null || true
rm -rf "$FORK_MARKERS"
wait_port_free "$PORT"

echo ""
echo "--- 6c: --backend auto routes an f16/quant=none vindex (per its index.json) to the shim backend ---"
PORT=18608
F16_VINDEX=$(mktemp -d)
cat > "$F16_VINDEX/index.json" <<'JSONEOF'
{"version": 2, "dtype": "f16", "quant": "none"}
JSONEOF
out=$(bash "$BS" start --vindex "$F16_VINDEX" --port "$PORT" --backend auto 2>&1)
rc=$?
assert_exit "auto-detected start exits 0" "0" "$rc"
assert_contains "auto resolves f16/quant=none to shim" "ready (pid" "$out"
backend_field=$(cut -f2 "$(pidfile_for "$PORT")")
assert_true "pidfile records backend=shim" "$([ "$backend_field" = "shim" ] && echo 1 || echo 0)"
bash "$BS" stop --port "$PORT" >/dev/null 2>&1
wait_port_free "$PORT"
rm -rf "$F16_VINDEX"

echo ""
echo "--- 6d: --backend auto routes a non-f16/quantized vindex to the larql-serve backend ---"
PORT=18609
Q4K_VINDEX=$(mktemp -d)
cat > "$Q4K_VINDEX/index.json" <<'JSONEOF'
{"version": 2, "dtype": "q4k", "quant": "q4k"}
JSONEOF
out=$(bash "$BS" start --vindex "$Q4K_VINDEX" --port "$PORT" --backend auto 2>&1)
rc=$?
assert_exit "auto-detected start exits 0" "0" "$rc"
backend_field=$(cut -f2 "$(pidfile_for "$PORT")")
assert_true "pidfile records backend=larql (not f16/quant=none)" "$([ "$backend_field" = "larql" ] && echo 1 || echo 0)"
bash "$BS" stop --port "$PORT" >/dev/null 2>&1
wait_port_free "$PORT"
rm -rf "$Q4K_VINDEX"

echo ""
echo "--- 7: no pattern-based process discovery/kill anywhere in the script ---"
if grep -nE 'pkill|pgrep.*kill|kill .*\$\(' "$BS" > /tmp/vindex-serve-pattern-kill-check.$$; then
  echo "  FAIL: found pattern-kill-shaped code:"
  cat /tmp/vindex-serve-pattern-kill-check.$$
  ((FAIL++)) || true
else
  echo "  PASS: no pattern-kill-shaped code in bin/vindex-serve"; ((PASS++)) || true
fi
rm -f /tmp/vindex-serve-pattern-kill-check.$$

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
rm -rf "$WORKDIR"
[ "$FAIL" -eq 0 ]
