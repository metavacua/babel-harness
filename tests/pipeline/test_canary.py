# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.canary import CANARY_PROMPTS, conservation_ok


def test_canary_set_is_fixed_and_nonempty():
    assert len(CANARY_PROMPTS) >= 4
    assert all(isinstance(p, str) and isinstance(t, str) for p, t in CANARY_PROMPTS)


def test_conservation_holds_within_tolerance():
    before = {"p1": ("Paris", 0.80)}
    after = {"p1": ("Paris", 0.70)}
    assert conservation_ok(before, after, tol=0.15)


def test_conservation_fails_on_top_token_flip():
    before = {"p1": ("Paris", 0.80)}
    after = {"p1": ("Pose", 0.60)}
    assert not conservation_ok(before, after, tol=0.15)


def test_conservation_fails_on_large_prob_drop():
    before = {"p1": ("Paris", 0.80)}
    after = {"p1": ("Paris", 0.55)}
    assert not conservation_ok(before, after, tol=0.15)


def test_conservation_fails_when_prompt_missing_from_after():
    before = {"p1": ("Paris", 0.80)}
    after: dict = {}
    assert not conservation_ok(before, after, tol=0.15)
