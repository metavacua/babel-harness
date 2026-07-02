# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.lql_driver import build_insert_script, parse_infer, parse_describe

def test_insert_script_grammar_exact():
    s = build_insert_script(
        vindex="/v/x.vindex",
        edge={"s": "docs/a.md", "r": "part-of-matter", "o": "matter:coop"},
        layer=26, mode="COMPOSE", alpha=0.12, patch_path="/p/step-1.vlp")
    assert 'USE "/v/x.vindex";' in s
    assert "BEGIN PATCH;" in s
    assert ('INSERT INTO EDGES (entity, relation, target) '
            'VALUES ("docs/a.md", "part-of-matter", "matter:coop") '
            'AT LAYER 26 ALPHA 0.12 MODE COMPOSE;') in s
    assert 'SAVE PATCH "/p/step-1.vlp";' in s

def test_knn_mode_omits_alpha():
    s = build_insert_script(vindex="/v", edge={"s": "a", "r": "b", "o": "c"},
                            layer=20, mode="KNN", alpha=None, patch_path="/p.vlp")
    assert "ALPHA" not in s and "MODE KNN" in s

def test_parse_infer_lines():
    raw = 'larql> INFER "x" TOP 3;\n  1. Paris (60.5%)\n  2. Lyon (10.2%)\n'
    out = parse_infer(raw)
    assert out[0] == ("Paris", 0.605) and out[1] == ("Lyon", 0.102)

def test_parse_describe_lines():
    raw = 'docs/a.md --part-of-matter--> matter:coop  (0.90, L26)\n'
    out = parse_describe(raw)
    assert ("docs/a.md", "part-of-matter", "matter:coop") in out
