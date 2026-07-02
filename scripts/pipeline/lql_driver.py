# SPDX-License-Identifier: AGPL-3.0-or-later
"""cli-lql mutation driver: LQL scripts piped to `larql repl` under containment.

Grammar per canonical spec (crates/larql-lql/docs/spec.md §3.5):
INSERT INTO EDGES (entity, relation, target) VALUES (e, r, t)
    [AT LAYER n] [CONFIDENCE f] [ALPHA f] [MODE {KNN|COMPOSE}];
NOTE: run the grammar self-check in Task 12 step 1 before first real use;
if canonical output format differs, fix the parsers HERE (single point).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.pipeline.contained import run_serial

_INFER_LINE = re.compile(r"^\s*\d+\.\s+(\S+)\s+\((\d+(?:\.\d+)?)%\)")
_DESCRIBE_LINE = re.compile(r"^(\S+)\s+--([\w\-]+)-->\s+(\S+)")


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def build_insert_script(vindex: str, edge: dict, layer: int, mode: str,
                        alpha: float | None, patch_path: str) -> str:
    alpha_clause = f" ALPHA {alpha}" if (alpha is not None and mode == "COMPOSE") else ""
    return (f"USE {_q(vindex)};\n"
            f"BEGIN PATCH;\n"
            f"INSERT INTO EDGES (entity, relation, target) "
            f"VALUES ({_q(edge['s'])}, {_q(edge['r'])}, {_q(edge['o'])}) "
            f"AT LAYER {layer}{alpha_clause} MODE {mode};\n"
            f"SAVE PATCH {_q(patch_path)};\n")


def parse_infer(raw: str) -> list[tuple[str, float]]:
    out = []
    for line in raw.splitlines():
        m = _INFER_LINE.match(line)
        if m:
            out.append((m.group(1), float(m.group(2)) / 100.0))
    return out


def parse_describe(raw: str) -> list[tuple[str, str, str]]:
    out = []
    for line in raw.splitlines():
        m = _DESCRIBE_LINE.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
    return out


@dataclass
class StepResult:
    ok: bool
    raw: str


class CliLqlDriver:
    name = "cli-lql"

    def __init__(self, larql_bin: str, vindex: str,
                 mem_mb: int = 2500, cpus: int = 6, timeout: int = 900):
        self.bin, self.vindex = larql_bin, vindex
        self.mem_mb, self.cpus, self.timeout = mem_mb, cpus, timeout

    def _repl(self, script: str) -> str:
        r = run_serial([self.bin, "repl"], timeout=self.timeout,
                       mem_mb=self.mem_mb, cpus=self.cpus, input=script)
        return (r.stdout or "") + "\n" + (r.stderr or "")

    def insert_step(self, edge: dict, layer: int, mode: str,
                    alpha: float | None, patch_path: str) -> StepResult:
        raw = self._repl(build_insert_script(self.vindex, edge, layer, mode,
                                             alpha, patch_path))
        ok = ("Error" not in raw) and ("error" not in raw.lower().split("warning")[0])
        return StepResult(ok=ok, raw=raw)

    def describe(self, entity: str) -> list[tuple[str, str, str]]:
        return parse_describe(self._repl(
            f"USE {_q(self.vindex)};\nDESCRIBE {_q(entity)};\n"))

    def infer(self, prompt: str, top: int = 5) -> list[tuple[str, float]]:
        return parse_infer(self._repl(
            f"USE {_q(self.vindex)};\nINFER {_q(prompt)} TOP {top};\n"))

    def remove_patch(self, patch_path: str) -> bool:
        raw = self._repl(f"USE {_q(self.vindex)};\nREMOVE PATCH {_q(patch_path)};\n")
        return "Error" not in raw
