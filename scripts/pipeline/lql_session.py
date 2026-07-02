# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared LQL session-batching helpers -- Task 13 adaptation A.

Extracted from scripts/run_base_cases.py (where they were validated live
against the canonical smollm2 vindex in Task 12) so run_base_cases.py and
the finite-induction harness (scripts/pipeline/induct.py) share ONE tested
implementation. The Task 12 review flagged the unexported/untested copies
as an Important finding; this module closes it.

Everything here is stdlib-only and parses raw `larql repl` output whose
formats are pinned in scripts/pipeline/lql_driver.py against
larql-canonical @ ab7a08c1 (the Rust source is the single source of truth).
"""
from __future__ import annotations

import re

from scripts.pipeline.canary import CANARY_PROMPTS
from scripts.pipeline.lql_driver import parse_infer

# INFER TOP used uniformly for all canary measurements (Task 12 frozen value).
CANARY_TOP = 3

# One "Predictions (walk FFN):" header per INFER call --
# crates/larql-lql/src/executor/query/infer.rs pushes exactly one, before
# its numbered rows.
_INFER_HEADER = "Predictions (walk FFN):"

# "  Nms" in-tool latency lines emitted by INFER (infer.rs).
_MS_LINE = re.compile(r"^\s*(\d+(?:\.\d+)?)ms\s*$")

# Line-anchored REPL error prefix -- crates/larql-cli eprintln!("Error: {e}").
_ERROR_LINE = re.compile(r"^Error:")


def q(s: str) -> str:
    """Quote a string for LQL (double quotes, backslash-escaped)."""
    return '"' + s.replace('"', '\\"') + '"'


def canonical_prompt(entity: str, relation: str) -> str:
    """The binary's own KNN insert-key template, byte-exact vs
    larql-canonical crates/larql-lql/src/executor/tuning.rs:195-198:

        let rel_words = relation.replace(['-', '_'], " ");
        format!("The {rel_words} of {entity} is")

    KNN generation checks MUST probe with this exact form: the stored key is
    the residual of this prompt, and the override only fires when the probe
    residual is cosine-similar (>0.75) to it (anomaly-B finding, verified
    live: 100.00% / cos=0.82 on the template prompt, invisible otherwise).
    """
    rel_words = relation.replace("-", " ").replace("_", " ")
    return f"The {rel_words} of {entity} is"


def has_error_line(raw: str) -> bool:
    """True iff any line carries the REPL's line-anchored `Error:` prefix.
    A warning line merely containing the substring "error" does not count."""
    return any(_ERROR_LINE.match(line) for line in raw.splitlines())


def split_infer_blocks(raw: str) -> list[str]:
    """Split raw multi-statement REPL output into one substring per INFER
    call, on its deterministic header (crates/larql-lql/src/executor/query/
    infer.rs pushes exactly one "Predictions (walk FFN):" line per INFER,
    before its numbered rows). Positional/count-based chunking was tried
    first and is UNSAFE: a rank >=2 row can be an empty/whitespace-only
    token (confirmed live -- see tests/pipeline/fixtures/
    preflight-canary-batch.log, "Water is made of hydrogen and" TOP 3 rank 2
    is `                     (0.40%)`, an empty token that `_INFER_LINE`'s
    `(\\S+)` group does not match, so parse_infer silently drops that ONE
    row and a TOP-3 call yields only 2 parsed rows). Header-splitting
    sidesteps row-count entirely (preflight finding F4)."""
    idxs = [m.start() for m in re.finditer(re.escape(_INFER_HEADER), raw)]
    if not idxs:
        return []
    idxs.append(len(raw))
    return [raw[idxs[i]:idxs[i + 1]] for i in range(len(idxs) - 1)]


def chunk_infer_rows(raw: str, n_statements: int) -> list[list[tuple[str, float]]]:
    """Parse one row-list per INFER statement issued in the script that
    produced `raw`, by header-splitting (see split_infer_blocks) rather than
    assuming each statement returns exactly its requested TOP row count.
    Raises loudly if the number of "Predictions (walk FFN):" headers found
    does not match the number of INFER statements actually issued (e.g. a
    statement errored before producing output) instead of silently
    misattributing rows to the wrong prompt."""
    blocks = split_infer_blocks(raw)
    if len(blocks) != n_statements:
        raise ValueError(
            f"chunk_infer_rows: expected {n_statements} INFER blocks, "
            f"got {len(blocks)}; raw tail: {raw[-300:]!r}")
    return [parse_infer(b) for b in blocks]


# KNN-override row -- crates/larql-inference/src/forward/infer_patched.rs
# KnnOverride.token is the FULL stored target string
# (larql-vindex/src/patch/knn_store.rs::target_token = target.to_string()),
# printed by infer.rs as format!("   1. {:20} (100.00%, {})", ovr.token,
# summary). A token containing spaces (e.g. a has-title value) makes the row
# UNMATCHABLE by lql_driver._INFER_LINE's (\S+) group -- the row is silently
# dropped from parse_infer output (same failure class as preflight F4), so
# override detection must anchor on the deterministic
# "(100.00%, source=knn_override" marker instead of on row parsing.
_OVERRIDE_ROW = re.compile(
    r"^\s*1\.\s+(?P<tok>.*?)\s+\(100\.00%, source=knn_override", re.M)


def parse_knn_override(block: str) -> str | None:
    """Return the override row's token (the full stored target string,
    whitespace-trimmed) if a KNN override fired in this INFER block, else
    None. Complements parse_infer, which cannot see spaced-token override
    rows at all."""
    m = _OVERRIDE_ROW.search(block)
    return m.group("tok") if m else None


def extract_ms_lines(raw: str) -> list[float]:
    """In-tool-reported per-statement latency ("  Nms" lines emitted by
    INFER; see crates/larql-lql/src/executor/query/infer.rs). Supplementary
    to the Python-side wall-clock latency recorded around each subprocess
    call -- narrows down how much of a call's latency is LQL compute vs.
    process/mmap overhead."""
    return [float(m.group(1)) for line in raw.splitlines()
            if (m := _MS_LINE.match(line))]


def split_describe_blocks(raw: str, entities: list[str]) -> dict[str, str]:
    """Attribute a multi-DESCRIBE session's raw output to its entities.

    DESCRIBE output starts with the bare entity string on its own line
    (crates/larql-lql/src/executor/query/describe/exec.rs: the first output
    element is `entity`; confirmed live in
    tests/pipeline/fixtures/preflight-session3-describe.log), followed by
    indented detail rows. parse_describe() alone cannot attribute arrow
    rows to entities when a session issues several DESCRIBEs, so this
    slices the raw into per-entity blocks first.

    Block boundaries: the next requested entity's own line, the first INFER
    header (DESCRIBEs precede INFERs in induction sessions -- this keeps
    Inference-trace arrow lines out of the last block), or end of output.
    An entity whose line never appears is absent from the result (callers
    treat that as browse-miss, never as silent success).
    """
    lines = raw.splitlines()
    positions: dict[str, int] = {}
    idx = 0
    for ent in entities:
        for j in range(idx, len(lines)):
            if lines[j] == ent:
                positions[ent] = j
                idx = j + 1
                break
    header_idx = next(
        (j for j, line in enumerate(lines) if line == _INFER_HEADER),
        len(lines))
    boundaries = sorted(list(positions.values()) + [header_idx, len(lines)])
    blocks: dict[str, str] = {}
    for ent, start in positions.items():
        end = min(b for b in boundaries if b > start)
        blocks[ent] = "\n".join(lines[start:end])
    return blocks


def measure_canaries_batch(driver, prelude: list[str] | None = None
                           ) -> tuple[dict[str, tuple[str, float]], str, float]:
    """Measure all CANARY_PROMPTS in ONE session (one subprocess call).
    `prelude` statements (e.g. APPLY PATCH) run first, in the same session,
    before the INFER battery -- so the canary measurement reflects whatever
    patch state the prelude establishes. `driver` needs only a
    run_script(statements) -> (raw, latency) method."""
    stmts = list(prelude or [])
    for prompt, _expected in CANARY_PROMPTS:
        stmts.append(f"INFER {q(prompt)} TOP {CANARY_TOP};")
    raw, latency = driver.run_script(stmts)
    groups = chunk_infer_rows(raw, len(CANARY_PROMPTS))
    out: dict[str, tuple[str, float]] = {}
    for (prompt, _expected), preds in zip(CANARY_PROMPTS, groups):
        out[prompt] = preds[0] if preds else ("", 0.0)
    return out, raw, latency
