#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAI-compat chat shim: canonical-template INFER through the patched
cli-lql driver, giving the chat path TRUE ablation control.

Motivation (larql-to-sparql#253; scripts/pipeline/matrix.py's "Chat caveat"):
`larql serve`'s /v1/chat/completions route panics on f16 vindexes and, even
where it doesn't, has no per-request mechanism to APPLY a patch overlay --
so Task 14's chat cells could only ever read the unpatched base vindex.
This shim sidesteps `larql serve` entirely: each POST /v1/chat/completions
opens ONE cli-lql repl session (CliLqlDriver.run_script) that first APPLY
PATCHes the configured overlay chain (empty in ablation mode) and then
issues a single INFER -- so the chat path gets the SAME session-scoped
patched-vs-ablation control the browse/infer cells already have (findings
F2/F3, scripts/pipeline/induct.py: patches are session-scoped, so a fresh
session that never APPLYs a patch can never observe it -- ablation is
therefore just an empty `patches` list, not a separate code path that could
silently diverge from the patched one).

Prompt-templating contract: this shim does NOT re-template the incoming
message. The LAST `role: "user"` message's `content` is passed VERBATIM as
the INFER prompt string. Goose-side prompts are expected to already BE
canonical-template prompts (lql_session.canonical_prompt's "The {rel words}
of {entity} is" form, byte-exact vs tuning.rs) -- that templating is the
ORCHESTRATOR's responsibility, never this shim's.

stdlib only: http.server.ThreadingHTTPServer. The underlying driver is a
serial subprocess resource (one repl session at a time, mirroring
run_serial's C4 containment invariant), so every request serializes on a
single threading.Lock around the driver call -- single-worker semantics,
by design, not an oversight.

Streaming (goose finding, task 14b): a live capture showed goose always
POSTs `"stream": true` (for both the session-title side-request and the
main turn) and aborts the connection -- observed as a server-side
BrokenPipeError -- the moment it reads a plain (non-SSE) JSON body back.
So `stream: true` gets a real OpenAI-shape SSE response: `Content-Type:
text/event-stream`, one `data: {...}\n\n` chunk carrying `delta:
{"role":"assistant"}`, one carrying `delta: {"content": <answer>}`, one
terminal chunk with `finish_reason: "stop"` and an empty delta, then
`data: [DONE]\n\n`. The answer itself is computed exactly the same way as
the non-streaming path (one patched INFER via CliLqlDriver.run_script) --
streaming only changes how the SAME answer is framed on the wire, never
what is computed. `stream` absent/false keeps the original plain-JSON
response byte-for-byte (curl and the non-stream tests are unaffected).
Any BrokenPipeError/ConnectionResetError while writing either response
shape is caught and logged to stderr, never left to crash the handler
thread -- see Handler._write_json / Handler._write_event_stream.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Repo-root bootstrap: this file is invoked directly as
# `python3 scripts/pipeline/chat_shim.py ...` (the orchestrator's live run),
# which puts scripts/pipeline/ on sys.path[0], NOT the repo root -- so the
# `scripts.pipeline.*` absolute imports below would otherwise raise
# ModuleNotFoundError before __main__ even runs. Same convention as
# scripts/run_matrix.py's bootstrap, one directory deeper (parent.parent
# .parent here vs. parent.parent there). A no-op under pytest/`python3 -m`,
# where the repo root is already on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.pipeline.contained import mem_available_mb  # noqa: E402
from scripts.pipeline.induct import size_mem_mb          # noqa: E402
from scripts.pipeline.lql_driver import CliLqlDriver, parse_infer  # noqa: E402
from scripts.pipeline.lql_session import q, split_infer_blocks    # noqa: E402

INFER_TOP = 5


class _BadRequest(Exception):
    """Malformed or invalid /v1/chat/completions request body -- carries a
    human-readable message that becomes the 400 JSON error's "message"."""


def build_stream_chunks(chat_id: str, created: int, model: str,
                        content: str) -> list[bytes]:
    """Serialize ONE already-computed answer as an OpenAI-compat SSE
    stream: a role-delta chunk, a content-delta chunk carrying the full
    answer, a terminal empty-delta `finish_reason: "stop"` chunk, and the
    `data: [DONE]\\n\\n` sentinel -- see the module docstring's streaming
    section. Pure (no I/O, no sockets) so it is unit-testable on its own,
    independent of whether a real disconnect can be simulated reliably
    (see test_chat_shim.py's test_build_stream_chunks_* tests).
    """
    def _event(delta: dict, finish_reason: str | None) -> bytes:
        payload = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta,
                        "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n".encode("utf-8")

    return [
        _event({"role": "assistant"}, None),
        _event({"content": content}, None),
        _event({}, "stop"),
        b"data: [DONE]\n\n",
    ]


class ChatShim:
    """OpenAI-compat /v1/chat/completions backed by CliLqlDriver.run_script.

    `patches` is a list of `.vlp` overlay path strings, APPLY PATCH'd in
    order in the SAME session as the INFER call. Ablation mode is simply an
    empty `patches` list -- see the module docstring for why that is the
    correct (and only session-safe) way to model it.

    `driver` needs only run_script(statements) -> (raw_output, latency);
    this is the exact interface CliLqlDriver and every FakeDriver in this
    codebase already speak (scripts/pipeline/lql_driver.py,
    tests/pipeline/test_induct.py).
    """

    def __init__(self, driver, patches: list[str], port: int,
                 model_id: str = "legal-theory-vindex"):
        self.driver = driver
        self.patches = list(patches)
        self.port = port
        self.model_id = model_id
        self.lock = threading.Lock()
        self.httpd: ThreadingHTTPServer | None = None

    # -- LQL plumbing --------------------------------------------------
    def _statements(self, content: str) -> list[str]:
        return ([f"APPLY PATCH {q(p)};" for p in self.patches]
                + [f"INFER {q(content)} TOP {INFER_TOP};"])

    def _infer(self, content: str) -> tuple[str, list[tuple[str, float]], float]:
        """Run the ONE patched-INFER session and return (top1, predictions,
        latency). Shared by both the plain-JSON and SSE-streamed response
        builders below -- streaming changes only the wire framing of this
        SAME computed answer, never the computation itself."""
        statements = self._statements(content)
        with self.lock:  # the driver is a serial subprocess resource
            raw, latency = self.driver.run_script(statements)
        blocks = split_infer_blocks(raw)
        predictions = parse_infer(blocks[-1]) if blocks else []
        top1 = predictions[0][0] if predictions else ""
        return top1, predictions, latency

    def _run_chat(self, content: str) -> dict:
        top1, predictions, latency = self._infer(content)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": top1},
                "finish_reason": "stop",
            }],
            # transparency extension: the raw prediction list this response
            # was derived from, and whether any patch overlay was applied.
            "larql": {
                "predictions": [[tok, prob] for tok, prob in predictions],
                "patched": bool(self.patches),
                "latency_s": round(latency, 3),
            },
        }

    def _run_chat_stream(self, content: str) -> list[bytes]:
        """Same computation as _run_chat, framed as SSE chunks instead of a
        single JSON body -- see build_stream_chunks and the module
        docstring's streaming section. The `larql` transparency extension
        has no OpenAI-streaming analogue, so it is intentionally dropped
        here (goose only ever reads choices[0].delta.content)."""
        top1, _predictions, _latency = self._infer(content)
        return build_stream_chunks(f"chatcmpl-{uuid.uuid4().hex}",
                                   int(time.time()), self.model_id, top1)

    # -- HTTP bodies -----------------------------------------------------
    def _models_body(self) -> dict:
        return {
            "object": "list",
            "data": [{
                "id": self.model_id,
                "object": "model",
                "owned_by": "larql-legal-theory",
                "created": int(time.time()),
            }],
        }

    @staticmethod
    def _last_user_content(body: dict) -> str | None:
        messages = body.get("messages")
        if not isinstance(messages, list):
            return None
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    return content
        return None

    def _parse_chat_request(self, raw_body: bytes) -> tuple[str, bool]:
        """Validate a raw request body, returning (user_content, stream).

        Raises _BadRequest(message) for anything that isn't valid JSON,
        isn't a JSON object, or has no `role: "user"` message with string
        content -- callers turn that into the 400 JSON error. `stream` is
        the body's top-level `"stream"` key coerced to bool (goose sends
        `"stream": true`; absent/false is the original plain-JSON path).
        """
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _BadRequest(f"malformed request body: {exc}") from exc
        if not isinstance(body, dict):
            raise _BadRequest("request body must be a JSON object")
        content = self._last_user_content(body)
        if content is None:
            raise _BadRequest("no \"messages\" entry with role \"user\" "
                              "and string content found")
        return content, bool(body.get("stream", False))

    # -- http.server plumbing --------------------------------------------
    def _handler_factory(self):
        shim = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # noqa: A003 -- stdlib hook
                pass  # keep test/CI output clean; nothing here is a signal

            def _write_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError) as exc:
                    # goose (or any client) can disconnect before/while we
                    # write -- log it and move on; never let it propagate
                    # out of the handler thread (see module docstring).
                    print(f"chat_shim: client disconnected writing JSON "
                          f"response ({type(exc).__name__}: {exc})",
                          file=sys.stderr)
                    self.close_connection = True

            def _write_event_stream(self, chunks: list[bytes]) -> None:
                """Write a `stream: true` response as OpenAI-compat SSE.
                No Content-Length/chunked framing is sent (the client is
                expected to read until EOF), so `Connection: close` is set
                and the socket is closed unconditionally afterwards."""
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in chunks:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    # the exact goose finding this shim was fixed for: it
                    # can abort mid-stream. Log and return -- never crash
                    # this handler thread or the ThreadingHTTPServer.
                    print(f"chat_shim: client disconnected mid-stream "
                          f"({type(exc).__name__}: {exc})", file=sys.stderr)
                finally:
                    self.close_connection = True

            def do_GET(self):  # noqa: N802 -- stdlib hook name
                if self.path == "/v1/models":
                    self._write_json(200, shim._models_body())
                else:
                    self._write_json(404, {"error": {"message": "not found"}})

            def do_POST(self):  # noqa: N802 -- stdlib hook name
                if self.path != "/v1/chat/completions":
                    self._write_json(404, {"error": {"message": "not found"}})
                    return
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw_body = self.rfile.read(length) if length else b""
                try:
                    content, stream = shim._parse_chat_request(raw_body)
                except _BadRequest as exc:
                    self._write_json(400, {"error": {"message": str(exc),
                                            "type": "invalid_request_error"}})
                    return
                try:
                    result = (shim._run_chat_stream(content) if stream
                             else shim._run_chat(content))
                except Exception as exc:  # noqa: BLE001 -- an HTTP endpoint
                    # must never crash on a driver surprise, and must leave
                    # NO poisoned state behind: the `with self.lock` in
                    # _infer above always releases via __exit__ even when
                    # this fires, so the very next request (streamed or
                    # not) is served normally.
                    self._write_json(500, {"error": {
                        "message": f"{type(exc).__name__}: {exc}",
                        "type": "driver_error"}})
                    return
                if stream:
                    self._write_event_stream(result)
                else:
                    self._write_json(200, result)

        return Handler

    def start(self) -> None:
        """Bind the listening socket (synchronous). Call serve_forever()
        afterwards, typically from a separate thread, to accept requests."""
        if self.httpd is None:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port),
                                             self._handler_factory())
            self.port = self.httpd.server_address[1]

    def serve_forever(self) -> None:
        self.start()
        try:
            self.httpd.serve_forever()
        finally:
            self.httpd.server_close()

    def shutdown(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()


def _load_patches(patches_dir: Path) -> list[str]:
    """Certified overlay patches from a step-*.vlp directory, sorted.

    No certificates.jsonl filtering is needed here (contrast
    scripts/pipeline/matrix.py's certified_patches): induct.py's rollback
    path deletes a step's .vlp file the moment I(n) fails (see
    run_induction's "rollback = non-application" handling), so every
    step-*.vlp file that survives ON DISK is, by construction, certified.
    """
    return [str(p) for p in sorted(patches_dir.glob("step-*.vlp"))]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="OpenAI-compat chat shim: patched INFER via cli-lql, "
                    "with true chat-path ablation control.")
    ap.add_argument("--bin", required=True, help="larql binary path")
    ap.add_argument("--vindex", required=True, help="vindex file path")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--patches-dir", type=Path, default=None,
                    help="directory of certified step-*.vlp overlays, "
                        "applied in sorted order before each INFER")
    ap.add_argument("--no-patches", action="store_true",
                    help="ablation mode: never APPLY any patch, even if "
                        "--patches-dir is given")
    ap.add_argument("--mem-mb", type=int, default=None,
                    help="cgroup memory ceiling (MiB); default: auto-sized "
                        "from live MemAvailable via "
                        "scripts.pipeline.induct.size_mem_mb")
    ap.add_argument("--timeout-s", type=int, default=900,
                    help="per-request repl subprocess timeout in seconds")
    ap.add_argument("--model-id", default="legal-theory-vindex")
    args = ap.parse_args()

    patches: list[str] = []
    if args.patches_dir is not None and not args.no_patches:
        patches = _load_patches(args.patches_dir)

    mem_mb = (args.mem_mb if args.mem_mb is not None
             else size_mem_mb(mem_available_mb()))
    driver = CliLqlDriver(args.bin, args.vindex, mem_mb=mem_mb,
                          timeout=args.timeout_s)
    shim = ChatShim(driver, patches, args.port, model_id=args.model_id)
    shim.start()
    mode = f"patched, {len(patches)} overlay(s)" if patches else "ablation, no patches"
    print(f"chat_shim: serving {shim.model_id!r} on :{shim.port} ({mode})")
    shim.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
