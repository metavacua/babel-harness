# SPDX-License-Identifier: AGPL-3.0-or-later
"""FakeDriver tests for the finite-induction harness (Task 13, adaptation H).

The FakeDriver speaks the driver's REAL session interface --
run_script(statements) -> (raw, latency) -- and emits byte-plausible
canonical output (same format strings the parsers are pinned to:
infer.rs / describe/format.rs / executor messages), so induct.py is tested
through the exact parse path used against the live binary, including:

- session-scoped patches (finding F2/F3): a patch is visible only in
  sessions that APPLY it; APPLY of a missing .vlp file errors like the
  real binary ("Error: Execution error: patch not found: <path>").
- entity-dominant KNN keys (anomaly-B finding): in `collide` mode the
  override returns the LAST-stored target for the prompt's ENTITY,
  regardless of relation -- the collision event Task 13 must detect.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.pipeline.canary import CANARY_PROMPTS
from scripts.pipeline.induct import run_induction, select_sequence, size_mem_mb
from scripts.pipeline.lam import LayerMap
from scripts.pipeline.lql_session import canonical_prompt

# ── frozen-calibration shape (mirrors ~/work/artifacts/calibration.json) ──
CAL = {
    "k_lo": 13, "k_hi": 25,
    "retrieval_threshold": 0.3,
    "canary_tolerance": 0.15,
    "mode": "KNN", "driver": "cli-lql", "alpha": None,
    "canary_baseline": {
        "The capital of France is": ["Paris", 0.2032],
        "Water is made of hydrogen and": ["oxygen", 0.9577],
        "Two plus two equals": ["four", 0.546],
        "The sun rises in the": ["east", 0.5751],
    },
}

_APPLY = re.compile(r'^APPLY PATCH "(.+)";$')
_BEGIN = re.compile(r'^BEGIN PATCH "(.+)";$')
_INSERT = re.compile(
    r'^INSERT INTO EDGES \(entity, relation, target\) '
    r'VALUES \("(.*)", "(.*)", "(.*)"\) AT LAYER (\d+)(?: ALPHA [\d.]+)? '
    r'MODE (\w+);$')
_DESCRIBE = re.compile(r'^DESCRIBE "(.+)";$')
_INFER = re.compile(r'^INFER "(.+)" TOP (\d+);$')


class FakeDriver:
    """Session-faithful fake of CliLqlDriver.run_script."""

    name = "fake"

    def __init__(self, hide_targets=(), collide=False,
                 canary_break_at=None, canary_flap=False):
        self.baseline = {k: tuple(v) for k, v in CAL["canary_baseline"].items()}
        self.patch_edges: dict[str, dict] = {}
        self.sessions: list[list[str]] = []
        self.insert_count = 0
        self.hide_targets = set(hide_targets)
        self.collide = collide
        self.canary_break_at = canary_break_at
        self.canary_flap = canary_flap
        self._flap_spent = False

    # ── canonical output emitters (formats pinned by test_lql_driver.py) ──
    @staticmethod
    def _infer_row(rank: int, tok: str, prob: float) -> str:
        return f"  {rank:2}. {tok:<20} ({prob * 100:.2f}%)"

    @staticmethod
    def _override_row(tok: str) -> str:
        return (f"   1. {tok:<20} (100.00%, source=knn_override/post_logits, "
                f"cos=0.82, L24, model_top1= a (8.43%))")

    @staticmethod
    def _brief_row(target: str) -> str:
        return f"    {'':<12} → {target:<20} {10.0:>7.1f}  L24 "

    def run_script(self, statements):
        self.sessions.append(list(statements))
        out = ["Using: /fake.vindex (32 layers, 81.9K features, model: /fake)"]
        session_edges: list[dict] = []
        pending_path = None
        pending_edge = None
        canary_bad = None  # decided lazily, held constant for the session
        for st in statements:
            if m := _APPLY.match(st):
                p = m.group(1)
                if not Path(p).exists():
                    out.append(f"Error: Execution error: patch not found: {p}")
                else:
                    session_edges.append(self.patch_edges[p])
                    out.append(f"Applied: {p} (1 operations: 1 inserts, "
                               f"0 updates, 0 deletes)")
            elif m := _BEGIN.match(st):
                pending_path = m.group(1)
                out.append(f"Patch session started: {pending_path}")
            elif m := _INSERT.match(st):
                pending_edge = {"s": m.group(1), "r": m.group(2), "o": m.group(3)}
                out.append(f"Inserted: {m.group(1)} —[{m.group(2)}]→ "
                           f"{m.group(3)} at L{m.group(4)} (KNN store)")
            elif st == "SAVE PATCH;":
                Path(pending_path).write_text(json.dumps(pending_edge))
                self.patch_edges[pending_path] = pending_edge
                session_edges.append(pending_edge)
                self.insert_count += 1
                out.append(f"Saved: {pending_path} (1 inserts, 0 updates, 0 deletes)")
            elif m := _DESCRIBE.match(st):
                ent = m.group(1)
                rows = [e for e in session_edges
                        if e["s"] == ent and e["o"] not in self.hide_targets]
                out.append(ent)
                if rows:
                    out.append(f"  signal: moderate ({len(rows)} edges, "
                               f"max gate 10.0)")
                    out.append("  Edges (L13-25):")
                    out.extend(self._brief_row(e["o"]) for e in rows)
                else:
                    out.append("  (no edges found)")
            elif m := _INFER.match(st):
                prompt = m.group(1)
                out.append("Predictions (walk FFN):")
                if prompt in self.baseline:
                    if canary_bad is None:
                        canary_bad = (self.canary_break_at is not None
                                      and self.insert_count >= self.canary_break_at
                                      and not (self.canary_flap and self._flap_spent))
                    tok, p = self.baseline[prompt]
                    if canary_bad:
                        tok = "the"     # top-token flip: conservation failure
                    out.append(self._infer_row(1, tok, p))
                    out.append(self._infer_row(2, "and", 0.01))
                else:
                    match = None
                    for e in session_edges:
                        if canonical_prompt(e["s"], e["r"]) == prompt:
                            match = e
                    if match is not None:
                        winner = match
                        if self.collide:
                            same_ent = [e for e in session_edges
                                        if e["s"] == match["s"]]
                            winner = same_ent[-1]
                        out.append(self._override_row(winner["o"]))
                        out.append(self._infer_row(2, "a", 0.0843))
                    else:
                        out.append(self._infer_row(1, ".", 0.10))
                out.append("  100ms")
        if canary_bad and self.canary_flap:
            self._flap_spent = True
        return "\n".join(out) + "\n", 0.01


def _e(s, r, o):
    return {"s": s, "r": r, "o": o, "c": 1.0}


EDGES = [
    _e("docs/m1/README.md", "part-of-matter", "matter:m1"),
    _e("docs/m2/README.md", "part-of-matter", "matter:m2"),
    _e("docs/a.md", "has-title", "Title Alpha"),
]
DEPTHS = {"START": 0, "docs/a.md": 1,
          "docs/m1/README.md": 2, "docs/m2/README.md": 2}


def _certs(out_dir):
    path = Path(out_dir) / "certificates.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _halt(out_dir):
    return json.loads((Path(out_dir) / "halt.json").read_text())


# ── select_sequence ──

def test_select_sequence_prioritizes_part_of_matter_then_depth():
    edges = [_e("docs", "contains", "docs/a.md"),
             _e("docs/a.md", "has-title", "Title Alpha"),
             _e("docs/m1/README.md", "part-of-matter", "matter:m1"),
             _e("LICENSE", "terminal", "END"),
             _e("START", "contains", "docs")]
    seq = select_sequence(edges, DEPTHS, n_max=10)
    assert seq[0]["r"] == "part-of-matter"
    assert all(e["r"] != "terminal" for e in seq)
    assert all(e["s"] != "START" for e in seq)


def test_select_sequence_caps_at_n_max_and_is_deterministic():
    seq2 = select_sequence(EDGES, DEPTHS, n_max=2)
    assert len(seq2) == 2
    assert seq2 == select_sequence(EDGES, DEPTHS, n_max=2)


# ── completes-N ──

def test_induction_completes_all_steps(tmp_path):
    drv = FakeDriver()
    lm = LayerMap(CAL["k_lo"], CAL["k_hi"], DEPTHS)
    seq = select_sequence(EDGES, DEPTHS, n_max=3)
    rep = run_induction(drv, lm, seq, CAL, tmp_path, budget_s=600, depths=DEPTHS)
    assert rep == {"completed": 3, "halted": False, "reason": "sequence-complete"}
    certs = _certs(tmp_path)
    assert len(certs) == 3
    assert all(c["I_n_ok"] for c in certs)
    assert _halt(tmp_path)["reason"] == "sequence-complete"
    assert (tmp_path / "mapping_meta.json").exists()
    for n in (1, 2, 3):
        assert (tmp_path / f"step-{n:04d}.vlp").exists()
    # all inserts pinned to the calibration KNN layer (k_hi - 1 = 24)
    inserts = [st for sess in drv.sessions for st in sess
               if st.startswith("INSERT INTO EDGES")]
    assert len(inserts) == 3
    assert all("AT LAYER 24" in st and "MODE KNN" in st for st in inserts)


def test_certificates_record_checked_gen_sample(tmp_path):
    drv = FakeDriver()
    seq = select_sequence(EDGES, DEPTHS, n_max=3)
    run_induction(drv, None, seq, CAL, tmp_path, budget_s=600, depths=DEPTHS)
    certs = _certs(tmp_path)
    # step n gen-checks the NEW edge plus the min(8, n-1) most recent priors
    assert certs[0]["checked_gen"] == [1]
    assert certs[1]["checked_gen"] == [2, 1]
    assert certs[2]["checked_gen"] == [3, 1, 2]
    for c in certs:
        assert all(not g["collision"] for g in c["gen_checks"])
        assert all(g["override_fired"] and g["target_match"]
                   for g in c["gen_checks"])


def test_verify_session_applies_all_patches_and_describes_dedup(tmp_path):
    drv = FakeDriver()
    seq = select_sequence(EDGES, DEPTHS, n_max=3)
    run_induction(drv, None, seq, CAL, tmp_path, budget_s=600, depths=DEPTHS)
    # session layout per step: [insert, verify]; step 3's verify session
    # must APPLY patches 1..3 and DESCRIBE each distinct entity once.
    verify3 = drv.sessions[-1]
    applies = [st for st in verify3 if st.startswith("APPLY PATCH")]
    describes = [st for st in verify3 if st.startswith("DESCRIBE")]
    assert len(applies) == 3
    assert len(describes) == 3          # 3 distinct entities
    assert len(set(describes)) == 3


# ── halts-and-rolls-back (non-application rollback, canonical bug #252) ──

def test_induction_halts_and_rolls_back_on_browse_failure(tmp_path):
    drv = FakeDriver(hide_targets={"matter:m2"})   # step 2's edge never browses
    seq = select_sequence(EDGES, DEPTHS, n_max=3)
    rep = run_induction(drv, None, seq, CAL, tmp_path, budget_s=600, depths=DEPTHS)
    assert rep["halted"] and rep["completed"] == 1
    halt = _halt(tmp_path)
    assert halt["halt_index"] == 2
    assert halt["failed_check"] == "browse_all"
    assert halt["rolled_back"] is True
    assert halt["restored"] is True
    # rollback = NON-APPLICATION: the failed step's .vlp is deleted,
    # prior steps' patches survive, and REMOVE PATCH is never issued.
    assert not (tmp_path / "step-0002.vlp").exists()
    assert (tmp_path / "step-0001.vlp").exists()
    assert all("REMOVE PATCH" not in st
               for sess in drv.sessions for st in sess)
    # restoration verified in a FRESH session applying only patches 1..n-1
    restore = drv.sessions[-1]
    assert 'APPLY PATCH "%s";' % (tmp_path / "step-0001.vlp") in restore
    assert all("step-0002.vlp" not in st for st in restore)
    assert not any(st.startswith(("BEGIN PATCH", "INSERT")) for st in restore)
    infers = [st for st in restore if st.startswith("INFER")]
    assert len(infers) == len(CANARY_PROMPTS)


# ── collision halts (entity-dominant keys) ──

def test_induction_halts_on_wrong_target_override_collision(tmp_path):
    edges = [_e("docs/f1.md", "part-of-matter", "matter:m1"),
             _e("docs/f1.md", "has-title", "Title One")]
    depths = {"START": 0, "docs/f1.md": 1}
    drv = FakeDriver(collide=True)
    seq = select_sequence(edges, depths, n_max=2)
    rep = run_induction(drv, None, seq, CAL, tmp_path, budget_s=600, depths=depths)
    assert rep["halted"] and rep["completed"] == 1
    halt = _halt(tmp_path)
    assert halt["halt_index"] == 2
    assert halt["failed_check"] == "no_collision"
    certs = _certs(tmp_path)
    gen2 = certs[1]["gen_checks"]
    # the PRIOR edge's canonical prompt now retrieves the NEW edge's target
    prior = [g for g in gen2 if g["n"] == 1][0]
    assert prior["collision"] is True
    assert prior["override_fired"] is True and prior["target_match"] is False
    assert not (tmp_path / "step-0002.vlp").exists()


# ── quarantine metadata (adaptation E: λ is metadata-only for KNN) ──

def test_overdeep_edge_still_inserted_with_null_lambda_metadata(tmp_path):
    edges = [_e("deep.md", "part-of-matter", "matter:mq")]
    depths = {"START": 0, "deep.md": 99}    # exceeds band width 13
    lm = LayerMap(CAL["k_lo"], CAL["k_hi"], depths)
    assert lm.layer(edges[0]) is None       # λ quarantines it...
    drv = FakeDriver()
    rep = run_induction(drv, lm, edges, CAL, tmp_path, budget_s=600, depths=depths)
    # ...but KNN mode inserts it anyway, at the calibration layer,
    # recording the quarantine as null metadata.
    assert rep["completed"] == 1 and not rep["halted"]
    cert = _certs(tmp_path)[0]
    assert cert["lambda_layer"] is None
    assert cert["insert_layer"] == 24
    assert cert["I_n_ok"]


def test_in_band_edge_records_lambda_metadata(tmp_path):
    lm = LayerMap(CAL["k_lo"], CAL["k_hi"], DEPTHS)
    seq = select_sequence(EDGES, DEPTHS, n_max=1)
    run_induction(FakeDriver(), lm, seq, CAL, tmp_path, budget_s=600,
                  depths=DEPTHS)
    cert = _certs(tmp_path)[0]
    # depth 2 -> k_lo + 1 = 14 (metadata only; insert still at 24)
    assert cert["lambda_layer"] == 14
    assert cert["insert_layer"] == 24


# ── budget halt ──

def test_budget_exhaustion_halts_before_any_insert(tmp_path):
    drv = FakeDriver()
    seq = select_sequence(EDGES, DEPTHS, n_max=3)
    rep = run_induction(drv, None, seq, CAL, tmp_path, budget_s=-1, depths=DEPTHS)
    assert rep == {"completed": 0, "halted": True,
                   "reason": "time-budget-exhausted"}
    halt = _halt(tmp_path)
    assert halt["halt_index"] == 1
    assert halt["reason"] == "time-budget-exhausted"
    assert not list(Path(tmp_path).glob("step-*.vlp"))


# ── canary fragility mitigation (adaptation G: flap detection) ──

def test_canary_flap_single_bad_reading_does_not_halt(tmp_path):
    drv = FakeDriver(canary_break_at=1, canary_flap=True)
    seq = select_sequence(EDGES, DEPTHS, n_max=2)
    rep = run_induction(drv, None, seq, CAL, tmp_path, budget_s=600, depths=DEPTHS)
    assert not rep["halted"] and rep["completed"] == 2
    cert1 = _certs(tmp_path)[0]
    assert cert1["I_n_ok"]
    flap = cert1["canary_flap"]
    assert flap["first_ok"] is False and flap["second_ok"] is True


def test_canary_persistent_failure_halts_with_both_readings(tmp_path):
    drv = FakeDriver(canary_break_at=1, canary_flap=False)
    seq = select_sequence(EDGES, DEPTHS, n_max=2)
    rep = run_induction(drv, None, seq, CAL, tmp_path, budget_s=600, depths=DEPTHS)
    assert rep["halted"] and rep["completed"] == 0
    halt = _halt(tmp_path)
    assert halt["halt_index"] == 1
    assert halt["failed_check"] == "conserved"
    cert1 = _certs(tmp_path)[0]
    flap = cert1["canary_flap"]
    assert flap["first_ok"] is False and flap["second_ok"] is False
    # restoration re-measure happened and is recorded (bad canaries persist
    # in this fake, so restoration legitimately reads False)
    assert halt["restored"] is False
    assert not (tmp_path / "step-0001.vlp").exists()


# ── resource-aware cgroup sizing (babel-harness#13 mitigation) ──

def test_size_mem_mb_floor_cap_and_reserve():
    # scarce host: never below the proven Task 12 envelope
    assert size_mem_mb(2000) == 2500
    assert size_mem_mb(3000) == 2500          # 3000 - 1200 < floor
    # mid: available minus host reserve
    assert size_mem_mb(4200) == 3000
    # plentiful: capped
    assert size_mem_mb(10000) == 3400


def test_size_mem_mb_monotonic_nondecreasing():
    vals = [size_mem_mb(a) for a in range(1000, 12000, 250)]
    assert vals == sorted(vals)
