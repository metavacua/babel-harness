# SPDX-License-Identifier: AGPL-3.0-or-later
import http.server
import threading
from scripts.pipeline.server import LarqlServer

class _Mock(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"data":[{"id":"m"}],"object":"list"}'
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        body = b'{"choices":[{"message":{"content":"ok"}}]}'
        self.send_response(200); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

def _serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def test_alive_and_models_and_chat_against_mock():
    srv = _serve()
    s = LarqlServer(larql_bin="/bin/true", vindex="/x", port=srv.server_address[1])
    assert s.alive()
    assert s.models() == ["m"]
    out = s.chat("hello", max_tokens=4)
    assert out["choices"][0]["message"]["content"] == "ok"
    srv.shutdown()

def test_alive_false_when_nothing_listening():
    s = LarqlServer(larql_bin="/bin/true", vindex="/x", port=59999)
    assert not s.alive()
