# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for scripts/spec_proof.py's B2 claim logic.

Root cause under test: B2 hardcoded the literal count "41 passed" rather than
the durable invariant (suite exits 0, zero failures). The coding-agent test
suite legitimately grew from 41 to 52 tests across 18+ TDD commits, which
made B2 report REFUTED despite the underlying suite being entirely healthy.

These tests exercise the pure decision logic directly (no subprocess), so
they run fast and deterministically.
"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SPEC_PROOF_PATH = REPO / "scripts" / "spec_proof.py"

spec = importlib.util.spec_from_file_location("spec_proof", SPEC_PROOF_PATH)
spec_proof = importlib.util.module_from_spec(spec)
sys.modules["spec_proof"] = spec_proof
spec.loader.exec_module(spec_proof)


def test_current_count_52_should_be_proven():
    """The actual current reality: 52 passed, 0 failed, exit 0 — must be PROVEN."""
    ok, evidence = spec_proof._interpret_coding_agent_test_output(
        0, "Some preamble\nResults: 52 passed, 0 failed\n"
    )
    assert ok is True, f"expected PROVEN for 52 passed/0 failed, got REFUTED: {evidence}"


def test_original_count_41_should_still_be_proven():
    """Historical count must still work — this is a generalization, not a magic-number swap."""
    ok, evidence = spec_proof._interpret_coding_agent_test_output(
        0, "Results: 41 passed, 0 failed\n"
    )
    assert ok is True, f"expected PROVEN for 41 passed/0 failed, got REFUTED: {evidence}"


def test_future_growth_to_100_should_be_proven():
    """Any future legitimate test growth must not re-break this claim."""
    ok, evidence = spec_proof._interpret_coding_agent_test_output(
        0, "Results: 100 passed, 0 failed\n"
    )
    assert ok is True, f"expected PROVEN for 100 passed/0 failed, got REFUTED: {evidence}"


def test_actual_failures_must_still_be_refuted():
    """A real regression (nonzero failures) must still be caught — not just always-true."""
    ok, evidence = spec_proof._interpret_coding_agent_test_output(
        1, "Results: 47 passed, 5 failed\n"
    )
    assert ok is False, f"expected REFUTED for a real regression, got PROVEN: {evidence}"


def test_nonzero_exit_with_clean_looking_output_must_be_refuted():
    """Exit code matters independently of the passed/failed text (e.g. crash after printing)."""
    ok, evidence = spec_proof._interpret_coding_agent_test_output(
        1, "Results: 52 passed, 0 failed\n"
    )
    assert ok is False, f"expected REFUTED when exit code is nonzero, got PROVEN: {evidence}"


def test_zero_tests_ran_must_be_refuted():
    """0 passed, 0 failed (e.g. suite didn't actually run any tests) must not be a false PROVEN."""
    ok, evidence = spec_proof._interpret_coding_agent_test_output(
        0, "Results: 0 passed, 0 failed\n"
    )
    assert ok is False, f"expected REFUTED when zero tests actually ran, got PROVEN: {evidence}"
