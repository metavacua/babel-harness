# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contained larql-server lifecycle + OpenAI-compat client. ONE server max (C4)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request

from scripts.pipeline.contained import HEADROOM_MB, contained_cmd, mem_available_mb


class LarqlServer:
    def __init__(self, larql_bin: str, vindex: str, port: int = 8282,
                 mem_mb: int = 2800, cpus: int = 6):
        self.bin, self.vindex, self.port = larql_bin, vindex, port
        self.mem_mb, self.cpus = mem_mb, cpus
        self.proc: subprocess.Popen | None = None

    # -- client side -------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def alive(self) -> bool:
        try:
            with urllib.request.urlopen(self._url("/v1/models"), timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def models(self) -> list[str]:
        with urllib.request.urlopen(self._url("/v1/models"), timeout=10) as r:
            return [m["id"] for m in json.load(r)["data"]]

    def chat(self, prompt: str, max_tokens: int = 32, timeout: int = 600) -> dict:
        model = self.models()[0]
        req = urllib.request.Request(
            self._url("/v1/chat/completions"),
            data=json.dumps({"model": model, "max_tokens": max_tokens,
                             "messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    # -- lifecycle ---------------------------------------------------------
    def start(self, wait_s: int = 120) -> None:
        if self.alive():
            raise RuntimeError(f"a server already answers on :{self.port} (C4: never two)")
        avail = mem_available_mb()
        if avail < self.mem_mb + HEADROOM_MB:
            raise MemoryError(f"MemAvailable {avail}MB < {self.mem_mb + HEADROOM_MB}MB")
        cmd = contained_cmd([self.bin, "serve", self.vindex, "--port", str(self.port)],
                            mem_mb=self.mem_mb, cpus=self.cpus)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if self.alive():
                return
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early: {self.proc.returncode}")
            time.sleep(2)
        self.stop()
        raise TimeoutError(f"server not ready in {wait_s}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=20)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
