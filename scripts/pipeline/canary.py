# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixed canary set + conservation check (the Monty-Hall constraint made operational:
inserted knowledge must REDISTRIBUTE probability mass, not blindly inject it --
the protected set's distribution is invariant within tolerance, verified per step).

CANARY_PROMPTS' second element is the naive a-priori expectation for a large,
well-trained LM -- NOT an assertion. Task 12 measures what smollm2-360m-canonical
ACTUALLY predicts (recorded in calibration.json's canary_baseline) and treats
that measured value as the baseline going forward; a 360M model is not expected
to reproduce these labels verbatim.
"""
from __future__ import annotations

CANARY_PROMPTS: list[tuple[str, str]] = [
    ("The capital of France is", "Paris"),
    ("Water is made of hydrogen and", "oxygen"),
    ("Two plus two equals", "four"),
    ("The sun rises in the", "east"),
]


def conservation_ok(before: dict, after: dict, tol: float) -> bool:
    """True iff every prompt in `before` keeps its top token in `after`, and its
    top-token probability does not drop by more than `tol`. A prompt missing
    from `after` (empty INFER result) is treated as probability 0.0 -- an
    unconditional conservation failure, never silently skipped."""
    for prompt, (tok_b, p_b) in before.items():
        tok_a, p_a = after.get(prompt, (None, 0.0))
        if tok_a != tok_b:
            return False
        if p_b - p_a > tol:
            return False
    return True
