# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the OpenAI-compat chat shim (fake driver ONLY -- no live model
command anywhere in this file; the orchestrator exercises the real binary
separately). FakeDriver speaks the real CliLqlDriver.run_script contract
(statements -> (raw, latency)) and emits byte-plausible canonical INFER
output (same format lql_driver._INFER_LINE / lql_session split_infer_blocks
are pinned to -- see tests/pipeline/test_induct.py's FakeDriver, which this
mirrors), so the shim is exercised through its real parse path.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request

from scripts.pipeline.chat_shim import ChatShim, _load_patches, build_stream_chunks


def _infer_row(rank: int, tok: str, prob: float) -> str:
    return f"  {rank:2}. {tok:<20} ({prob * 100:.2f}%)"


class FakeDriver:
    """Session-faithful fake of CliLqlDriver.run_script."""

    def __init__(self, top1=("Paris", 0.42), rows=(), delay=0.0, raise_on=None):
        self.sessions: list[list[str]] = []
        self.top1 = top1
        self.extra_rows = list(rows)
        self.delay = delay
        self.raise_on = raise_on  # callable(statements) -> bool
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def run_script(self, statements):
        self.sessions.append(list(statements))
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.raise_on is not None and self.raise_on(statements):
                raise RuntimeError("fake driver failure")
            tok, prob = self.top1
            lines = ["Predictions (walk FFN):", _infer_row(1, tok, prob)]
            for i, (t, p) in enumerate(self.extra_rows, start=2):
                lines.append(_infer_row(i, t, p))
            return "\n".join(lines) + "\n", 0.01
        finally:
            with self._lock:
                self.active -= 1


# ── test server helpers ──────────────────────────────────────────────────
def _start(driver, patches=(), model_id="legal-theory-vindex"):
    shim = ChatShim(driver, list(patches), port=0, model_id=model_id)
    shim.start()
    thread = threading.Thread(target=shim.serve_forever, daemon=True)
    thread.start()
    return shim, thread


def _stop(shim, thread):
    shim.shutdown()
    thread.join(timeout=5)


def _get(shim, path):
    with urllib.request.urlopen(
            f"http://127.0.0.1:{shim.port}{path}", timeout=5) as r:
        return r.status, json.load(r)


def _post_raw(shim, path, data: bytes):
    req = urllib.request.Request(
        f"http://127.0.0.1:{shim.port}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _post(shim, path, body: dict):
    return _post_raw(shim, path, json.dumps(body).encode("utf-8"))


def _chat_body(content: str) -> dict:
    return {"model": "whatever", "messages": [
        {"role": "system", "content": "you are a legal assistant"},
        {"role": "user", "content": content},
    ]}


# ── tests ─────────────────────────────────────────────────────────────────
def test_models_endpoint_shape():
    driver = FakeDriver()
    shim, thread = _start(driver, model_id="legal-theory-vindex")
    try:
        status, body = _get(shim, "/v1/models")
        assert status == 200
        assert body["object"] == "list"
        assert isinstance(body["data"], list) and len(body["data"]) == 1
        assert body["data"][0]["id"] == "legal-theory-vindex"
    finally:
        _stop(shim, thread)


def test_chat_returns_top1_content_and_predictions_extension():
    driver = FakeDriver(top1=("Paris", 0.42), rows=[("Lyon", 0.05)])
    shim, thread = _start(driver)
    try:
        status, body = _post(shim, "/v1/chat/completions",
                             _chat_body("The capital of France is"))
        assert status == 200
        assert body["choices"][0]["message"]["content"] == "Paris"
        assert body["choices"][0]["message"]["role"] == "assistant"
        preds = body["larql"]["predictions"]
        assert preds[0] == ["Paris", 0.42]
        assert preds[1] == ["Lyon", 0.05]
        assert body["larql"]["patched"] is False
    finally:
        _stop(shim, thread)


def test_last_user_message_extracted_verbatim_as_infer_prompt():
    driver = FakeDriver()
    shim, thread = _start(driver)
    try:
        body = {"messages": [
            {"role": "user", "content": "first question, ignored"},
            {"role": "assistant", "content": "an answer"},
            {"role": "user", "content": "The capital of France is"},
        ]}
        _post(shim, "/v1/chat/completions", body)
        [statements] = driver.sessions
        assert statements == ['INFER "The capital of France is" TOP 5;']
    finally:
        _stop(shim, thread)


def test_patches_applied_in_order_before_infer():
    driver = FakeDriver()
    patches = ["/p/step-0001.vlp", "/p/step-0002.vlp", "/p/step-0003.vlp"]
    shim, thread = _start(driver, patches=patches)
    try:
        status, body = _post(shim, "/v1/chat/completions",
                             _chat_body("The capital of France is"))
        assert status == 200
        [statements] = driver.sessions
        assert statements == [
            'APPLY PATCH "/p/step-0001.vlp";',
            'APPLY PATCH "/p/step-0002.vlp";',
            'APPLY PATCH "/p/step-0003.vlp";',
            'INFER "The capital of France is" TOP 5;',
        ]
        assert body["larql"]["patched"] is True
    finally:
        _stop(shim, thread)


def test_ablation_mode_applies_no_patches():
    driver = FakeDriver()
    # ablation: constructed with an EMPTY patches list, exactly as main()'s
    # --no-patches flag does -- same code path as the patched case, just
    # with nothing to apply.
    shim, thread = _start(driver, patches=[])
    try:
        status, body = _post(shim, "/v1/chat/completions",
                             _chat_body("The capital of France is"))
        assert status == 200
        [statements] = driver.sessions
        assert all(not s.startswith("APPLY PATCH") for s in statements)
        assert statements == ['INFER "The capital of France is" TOP 5;']
        assert body["larql"]["patched"] is False
    finally:
        _stop(shim, thread)


def test_malformed_body_returns_400():
    driver = FakeDriver()
    shim, thread = _start(driver)
    try:
        status, body = _post_raw(shim, "/v1/chat/completions", b"{not json")
        assert status == 400
        assert "error" in body
        assert not driver.sessions, "driver must not be invoked on malformed input"
    finally:
        _stop(shim, thread)


def test_body_without_user_message_returns_400():
    driver = FakeDriver()
    shim, thread = _start(driver)
    try:
        status, body = _post(shim, "/v1/chat/completions",
                             {"messages": [{"role": "system", "content": "hi"}]})
        assert status == 400
        assert "error" in body
        assert not driver.sessions
    finally:
        _stop(shim, thread)


def test_driver_exception_returns_500_and_next_request_still_succeeds():
    calls = {"n": 0}

    def raise_once(_statements):
        calls["n"] += 1
        return calls["n"] == 1

    driver = FakeDriver(raise_on=raise_once)
    shim, thread = _start(driver)
    try:
        status, body = _post(shim, "/v1/chat/completions",
                             _chat_body("The capital of France is"))
        assert status == 500
        assert "error" in body
        assert "fake driver failure" in body["error"]["message"]

        # the lock must not be left poisoned/held -- the next request works.
        status2, body2 = _post(shim, "/v1/chat/completions",
                               _chat_body("The capital of France is"))
        assert status2 == 200
        assert body2["choices"][0]["message"]["content"] == "Paris"
    finally:
        _stop(shim, thread)


def test_concurrent_posts_are_serialized_no_interleaving():
    driver = FakeDriver(delay=0.2)
    shim, thread = _start(driver)
    results = []

    def worker():
        status, body = _post(shim, "/v1/chat/completions",
                             _chat_body("The capital of France is"))
        results.append(status)

    try:
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert results == [200, 200]
        assert len(driver.sessions) == 2
        assert driver.max_active == 1, (
            "driver.run_script was entered concurrently -- the shim's "
            "lock failed to serialize requests")
    finally:
        _stop(shim, thread)


# ── streaming (goose "stream": true, task 14b) ───────────────────────────
def _chat_body_stream(content: str) -> dict:
    return {**_chat_body(content), "stream": True}


def _sse_events(raw: str) -> list[str]:
    """Split a raw SSE body into its `data: ...` lines, dropping the
    trailing empty piece produced by the final `\n\n`."""
    return [part for part in raw.split("\n\n") if part]


def test_build_stream_chunks_shape():
    """Pure unit test of the SSE chunk serializer, independent of any
    socket/HTTP plumbing -- see the module's streaming contract: role
    delta, content delta, empty-delta stop, then the [DONE] sentinel."""
    chunks = build_stream_chunks("chatcmpl-abc123", 1_700_000_000,
                                 "legal-theory-vindex", "Paris")
    assert len(chunks) == 4
    assert chunks[-1] == b"data: [DONE]\n\n"

    events = []
    for raw in chunks[:-1]:
        assert raw.startswith(b"data: ") and raw.endswith(b"\n\n")
        events.append(json.loads(raw[len(b"data: "):-2]))

    for ev in events:
        assert ev["object"] == "chat.completion.chunk"
        assert ev["id"] == "chatcmpl-abc123"
        assert ev["created"] == 1_700_000_000
        assert ev["model"] == "legal-theory-vindex"
        assert ev["choices"][0]["index"] == 0

    assert events[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert events[0]["choices"][0]["finish_reason"] is None
    assert events[1]["choices"][0]["delta"] == {"content": "Paris"}
    assert events[1]["choices"][0]["finish_reason"] is None
    assert events[2]["choices"][0]["delta"] == {}
    assert events[2]["choices"][0]["finish_reason"] == "stop"


def test_stream_true_returns_sse_with_content_delta_and_done():
    driver = FakeDriver(top1=("Paris", 0.42))
    shim, thread = _start(driver)
    try:
        body = json.dumps(_chat_body_stream(
            "The capital of France is")).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", shim.port, timeout=5)
        try:
            conn.request("POST", "/v1/chat/completions", body=body,
                        headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("Content-Type") == "text/event-stream"
            raw = resp.read().decode("utf-8")
        finally:
            conn.close()

        lines = raw.rstrip("\n").split("\n")
        assert lines[-1] == "data: [DONE]"

        events = [json.loads(e[len("data: "):])
                 for e in _sse_events(raw) if e != "data: [DONE]"]
        assert events, "expected at least one SSE data event before [DONE]"
        assert all(ev["object"] == "chat.completion.chunk" for ev in events)

        deltas = [ev["choices"][0]["delta"] for ev in events]
        assert deltas[0] == {"role": "assistant"}
        assert {"content": "Paris"} in deltas
        finish_reasons = [ev["choices"][0]["finish_reason"] for ev in events]
        assert finish_reasons[-1] == "stop"
        assert deltas[-1] == {}

        # the driver still only ever sees the ordinary INFER statement --
        # streaming is purely a response-framing concern.
        [statements] = driver.sessions
        assert statements == ['INFER "The capital of France is" TOP 5;']
    finally:
        _stop(shim, thread)


def test_stream_false_still_returns_plain_json():
    """Explicit "stream": false must be indistinguishable from the
    original (no "stream" key) plain-JSON path."""
    driver = FakeDriver(top1=("Paris", 0.42))
    shim, thread = _start(driver)
    try:
        body = {**_chat_body("The capital of France is"), "stream": False}
        status, resp_body = _post(shim, "/v1/chat/completions", body)
        assert status == 200
        assert resp_body["choices"][0]["message"]["content"] == "Paris"
        assert resp_body["object"] == "chat.completion"
    finally:
        _stop(shim, thread)


def test_broken_pipe_client_disconnect_does_not_crash_server():
    """Best-effort simulation of the live goose finding: a client that
    sends a `stream: true` request and disappears before reading the
    response, forcing a BrokenPipe/ConnectionReset on the shim's write
    side. This IS reliably reproducible on Linux (a fully-closed client
    socket answers further server writes with RST -> EPIPE/ECONNRESET on
    the next send), but the exact OS-level timing is not guaranteed
    cross-platform -- so the assertion here is deliberately the robust,
    non-flaky one: the handler thread must not crash the server, and a
    normal subsequent request on a fresh connection must still succeed.
    (The SSE serialization itself is separately, deterministically
    covered by test_build_stream_chunks_shape above.)
    """
    driver = FakeDriver(top1=("Paris", 0.42), delay=0.05)
    shim, thread = _start(driver)
    try:
        body = json.dumps(_chat_body_stream(
            "The capital of France is")).encode("utf-8")
        request = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n" + body
        )
        s = socket.create_connection(("127.0.0.1", shim.port), timeout=5)
        try:
            s.sendall(request)
        finally:
            # abort WITHOUT reading any response -- the goose behavior.
            s.close()

        # give the handler thread time to run the (delayed) fake INFER and
        # attempt its now-doomed response write.
        time.sleep(0.5)

        status, resp_body = _post(shim, "/v1/chat/completions",
                                  _chat_body("The capital of France is"))
        assert status == 200
        assert resp_body["choices"][0]["message"]["content"] == "Paris"
    finally:
        _stop(shim, thread)


def test_load_patches_reads_step_vlp_sorted(tmp_path):
    (tmp_path / "step-0002.vlp").write_text("b")
    (tmp_path / "step-0001.vlp").write_text("a")
    (tmp_path / "step-0010.vlp").write_text("c")
    (tmp_path / "not-a-patch.txt").write_text("x")
    out = _load_patches(tmp_path)
    assert out == [
        str(tmp_path / "step-0001.vlp"),
        str(tmp_path / "step-0002.vlp"),
        str(tmp_path / "step-0010.vlp"),
    ]
