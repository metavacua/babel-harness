# SPDX-License-Identifier: AGPL-3.0-or-later
"""λ: graph depth -> model layer. STRICT layering (v2): order EMBEDDING, not compression.

λ(e) = k_lo + (depth(source(e)) - 1); None when depth exceeds the band width
(k_hi - k_lo + 1) — deeper chains are unrepresentable in one forward pass
(layer axis is a strict topological order; COMPOSE chains need strictly
increasing layers; AT LAYER is single-layer only) and are quarantined, never
compressed. Attribute-only nodes (cite:/category:/matter:) and KNN-stratum
edges use default_layer() = k_hi - 1 (knowledge.hi - 1): non-chaining
retrieval keys, exempt from strictness.
"""
from __future__ import annotations


class LayerMap:
    def __init__(self, k_lo: int, k_hi: int, depths: dict[str, int]):
        if not (0 <= k_lo <= k_hi):
            raise ValueError("need 0 <= k_lo <= k_hi")
        self.k_lo, self.k_hi = k_lo, k_hi
        self.depths = depths
        self.max_depth = max(depths.values()) if depths else 1

    def band_width(self) -> int:
        return self.k_hi - self.k_lo + 1

    def default_layer(self) -> int:
        return self.k_hi - 1

    def layer(self, edge: dict) -> int | None:
        d = self.depths.get(edge["s"])
        if d is None:
            return self.default_layer()
        if d < 1:               # START itself hosts no knowledge edge
            return self.k_lo
        if d > self.band_width():
            return None         # quarantine: chain deeper than the band
        return self.k_lo + (d - 1)

    def to_meta(self) -> dict:
        return {"formula": "strict-v2", "k_lo": self.k_lo, "k_hi": self.k_hi,
                "max_depth": self.max_depth}

    @classmethod
    def from_meta(cls, meta: dict, depths: dict[str, int]) -> "LayerMap":
        if meta.get("formula") != "strict-v2":
            raise ValueError(f"unknown λ formula: {meta.get('formula')}")
        return cls(meta["k_lo"], meta["k_hi"], depths)
