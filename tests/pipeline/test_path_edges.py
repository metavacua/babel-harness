# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.path_edges import path_derived_edges

FILES = [
    "docs/court-record/matters/cooperative-investment-law/evidence/coop-security.md",
    "docs/cross-cutting/patron-as-client.md",
    "papers/ai_and_ip/llm-database-theory/src/01.xml",
]

def test_directory_containment_chain():
    edges = path_derived_edges(FILES)
    assert any(e["r"] == "contains" and e["s"] == "docs" and e["o"] == "docs/court-record"
               for e in edges)
    assert any(e["r"] == "contains"
               and e["s"] == "docs/court-record/matters/cooperative-investment-law/evidence"
               and e["o"] == FILES[0] for e in edges)

def test_part_of_matter():
    edges = path_derived_edges(FILES)
    assert any(e["r"] == "part-of-matter" and e["s"] == FILES[0]
               and e["o"] == "matter:cooperative-investment-law" for e in edges)

def test_in_category():
    edges = path_derived_edges(FILES)
    assert any(e["r"] == "in-category" and e["s"] == FILES[1]
               and e["o"] == "category:cross-cutting" for e in edges)
    assert any(e["r"] == "in-category" and e["s"] == FILES[2]
               and e["o"] == "category:papers" for e in edges)

def test_deterministic():
    assert path_derived_edges(FILES) == path_derived_edges(list(reversed(FILES)))
