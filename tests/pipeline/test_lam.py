# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.lam import LayerMap

DEPTHS = {"START": 0, "docs": 1, "docs/a.md": 2, "docs/a.md#s1": 3}

def _e(s):
    return {"s": s, "r": "references", "o": "x", "c": 1.0, "prov": "t:0"}

def test_strict_direct_depth_mapping():
    lm = LayerMap(k_lo=14, k_hi=27, depths=DEPTHS)
    assert lm.layer(_e("docs")) == 14          # depth 1 -> k_lo
    assert lm.layer(_e("docs/a.md")) == 15     # depth 2 -> k_lo + 1
    assert lm.layer(_e("docs/a.md#s1")) == 16  # depth 3 -> k_lo + 2

def test_chain_is_order_embedding_no_collision():
    lm = LayerMap(k_lo=14, k_hi=27, depths=DEPTHS)
    layers = [lm.layer(_e(n)) for n in ("docs", "docs/a.md", "docs/a.md#s1")]
    assert layers == sorted(set(layers))       # strictly increasing, no collapse

def test_depth_beyond_band_quarantined():
    deep = {f"n{i}": i for i in range(0, 6)}   # depths 0..5
    lm = LayerMap(k_lo=10, k_hi=12, depths=deep)  # band width 3
    assert lm.layer(_e("n3")) == 12            # depth 3 = last representable
    assert lm.layer(_e("n4")) is None          # depth 4 > width -> quarantine
    assert lm.layer(_e("n5")) is None

def test_meta_roundtrip():
    lm = LayerMap(k_lo=14, k_hi=27, depths=DEPTHS)
    lm2 = LayerMap.from_meta(lm.to_meta(), DEPTHS)
    assert lm2.layer(_e("docs")) == lm.layer(_e("docs"))
    assert lm.to_meta()["formula"] == "strict-v2"

def test_attribute_nodes_use_default_layer():
    lm = LayerMap(k_lo=14, k_hi=27, depths=DEPTHS)
    assert lm.layer(_e("cite:somewhere")) == lm.default_layer() == 26
