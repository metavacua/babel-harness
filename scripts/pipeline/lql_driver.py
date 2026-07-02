# SPDX-License-Identifier: AGPL-3.0-or-later
"""cli-lql mutation driver: LQL scripts piped to `larql repl` under containment.

Grammar and output formats are pinned against larql-canonical @ ab7a08c1
(crates/larql-lql), NOT against the aspirational prose in spec.md — the
Rust source is the single source of truth. Sources cited inline below.
NOTE: run the grammar self-check in Task 12 step 1 before first real use;
if canonical output format differs, fix the parsers HERE (single point).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.pipeline.contained import run_serial

# ── INFER row formats ──
# Source: crates/larql-lql/src/executor/query/infer.rs
#   normal row:      format!("  {:2}. {:20} ({:.2}%)", i + 1, tok, prob * 100.0)
#   KNN-override row: format!("   1. {:20} (100.00%, {})", ovr.token, summary)
# `{:20}` pads (never truncates) a string to a minimum width of 20; the
# literal " (" in the template always emits exactly one space before "("
# regardless of token length, so `\s+` (one-or-more) always matches. The
# KNN-override row appends ", <summary>" before the closing paren, and the
# summary itself may contain nested parens (e.g. "model_top1=Paris (42.00%)")
# — the trailing `.*` is greedy so it backtracks to the LAST ")" in the
# line, which is always the correct outer paren.
_INFER_LINE = re.compile(r"^\s*\d+\.\s+(\S+)\s+\((\d+(?:\.\d+)?)%(?:,.*)?\)")

# ── DESCRIBE row format (Brief mode — the driver's default) ──
# Source: crates/larql-lql/src/executor/query/describe/format.rs::format_brief
#   format!("    {} → {:20} {:>7.1}  L{:<3}", label12, target, gate, layer)
# where label12 is 12 blank spaces when the edge carries no probe label.
# CliLqlDriver.describe() issues a bare `DESCRIBE "<entity>";` with no mode
# clause, so DescribeMode::default() == Brief (parser/query.rs; confirmed by
# parser/tests.rs: "brief is the default"). Brief rows have NO subject per
# row and NO (confidence, layer) tuple — just a trailing "<gate>  L<layer>".
# The target field is NOT quoted/escaped, so it may itself contain spaces;
# field boundaries are therefore derived from the trailing
# "<gate> L<layer>$" anchor rather than from naive whitespace-splitting —
# the non-greedy `.+?` for target expands only until the anchor matches,
# which correctly recovers multi-word targets.
_DESCRIBE_ROW = re.compile(
    r"^\s*(?P<label>\S(?:.*\S)?)?\s*→\s+(?P<target>.+?)\s+"
    r"(?P<gate>-?\d+(?:\.\d+)?)\s+L(?P<layer>\d+)\s*$"
)

# Source: crates/larql-cli/src/main.rs:603 and
# crates/larql-cli/src/commands/primary/run_cmd.rs:429,514 —
#   eprintln!("Error: {e}");
# The REPL's error prefix is a line-anchored "Error:" written to stderr
# (or, via the REPL loop, to stdout depending on call site). Match it
# line-by-line rather than as a raw substring.
_ERROR_LINE = re.compile(r"^Error:")


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def build_insert_script(vindex: str, edge: dict, layer: int, mode: str,
                        alpha: float | None, patch_path: str) -> str:
    # Grammar: crates/larql-lql/src/parser/patch.rs::parse_begin/parse_save
    # + ast.rs (~L159-169): BeginPatch { path: String } takes a REQUIRED
    # path; SavePatch is a unit variant with NO argument. The path moves
    # from SAVE to BEGIN. Confirmed by docs/spec.md §2.5 (~L559-576):
    #   BEGIN PATCH "medical-knowledge.vlp";
    #   ...
    #   SAVE PATCH;
    alpha_clause = f" ALPHA {alpha}" if (alpha is not None and mode == "COMPOSE") else ""
    return (f"USE {_q(vindex)};\n"
            f"BEGIN PATCH {_q(patch_path)};\n"
            f"INSERT INTO EDGES (entity, relation, target) "
            f"VALUES ({_q(edge['s'])}, {_q(edge['r'])}, {_q(edge['o'])}) "
            f"AT LAYER {layer}{alpha_clause} MODE {mode};\n"
            f"SAVE PATCH;\n")


def parse_infer(raw: str) -> list[tuple[str, float]]:
    out = []
    for line in raw.splitlines():
        m = _INFER_LINE.match(line)
        if m:
            out.append((m.group(1), float(m.group(2)) / 100.0))
    return out


def parse_describe(raw: str, entity: str) -> list[tuple[str, str, str]]:
    out = []
    for line in raw.splitlines():
        m = _DESCRIBE_ROW.match(line)
        if m:
            label = (m.group("label") or "").strip()
            target = m.group("target").strip()
            out.append((entity, label, target))
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
        script = build_insert_script(self.vindex, edge, layer, mode,
                                     alpha, patch_path)
        r = run_serial([self.bin, "repl"], timeout=self.timeout,
                       mem_mb=self.mem_mb, cpus=self.cpus, input=script)
        stdout = r.stdout or ""
        stderr = r.stderr or ""
        # stdout and stderr are captured as two SEPARATE buffers by
        # subprocess.run(capture_output=True) — they are NOT chronologically
        # interleaved with each other, so each stream is scanned
        # independently for a line matching the REPL's ^Error: prefix.
        # (No more substring "Error" / lowercase-after-"warning" heuristic:
        # a warning line that happens to mention "error" as a substring must
        # not mask, or be mistaken for, a real error line.)
        ok = (not any(_ERROR_LINE.match(line) for line in stdout.splitlines())
              and not any(_ERROR_LINE.match(line) for line in stderr.splitlines()))
        raw = stdout + "\n" + stderr
        return StepResult(ok=ok, raw=raw)

    def describe(self, entity: str) -> list[tuple[str, str, str]]:
        return parse_describe(self._repl(
            f"USE {_q(self.vindex)};\nDESCRIBE {_q(entity)};\n"), entity)

    def infer(self, prompt: str, top: int = 5) -> list[tuple[str, float]]:
        return parse_infer(self._repl(
            f"USE {_q(self.vindex)};\nINFER {_q(prompt)} TOP {top};\n"))

    def remove_patch(self, patch_path: str) -> bool:
        raw = self._repl(f"USE {_q(self.vindex)};\nREMOVE PATCH {_q(patch_path)};\n")
        return "Error" not in raw
