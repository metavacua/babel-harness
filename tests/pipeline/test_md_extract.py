# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.md_extract import extract_markdown, slugify

SAMPLE = """# Cooperative Security

## Findings

See [the membership security](the-membership-security.md) and
[formulas](../scratch/formulas.md#capacity).

### Sub-finding
"""

def test_h1_becomes_has_title():
    edges = extract_markdown(SAMPLE, "docs/a/coop.md")
    assert {"s": "docs/a/coop.md", "r": "has-title", "o": "Cooperative Security",
            "c": 1.0, "prov": "docs/a/coop.md:1"} in edges

def test_sections_nest_by_heading_level():
    edges = extract_markdown(SAMPLE, "docs/a/coop.md")
    assert any(e["r"] == "contains" and e["s"] == "docs/a/coop.md"
               and e["o"] == "docs/a/coop.md#findings" for e in edges)
    assert any(e["r"] == "contains" and e["s"] == "docs/a/coop.md#findings"
               and e["o"] == "docs/a/coop.md#sub-finding" for e in edges)

def test_relative_links_resolve_to_repo_paths():
    edges = extract_markdown(SAMPLE, "docs/a/coop.md")
    assert any(e["r"] == "references" and e["o"] == "docs/a/the-membership-security.md"
               for e in edges)
    assert any(e["r"] == "references" and e["o"] == "docs/scratch/formulas.md#capacity"
               for e in edges)

def test_deterministic_output():
    a = extract_markdown(SAMPLE, "docs/a/coop.md")
    b = extract_markdown(SAMPLE, "docs/a/coop.md")
    assert a == b

def test_code_fences_ignored():
    txt = "# T\n```\n# not a heading\n[not](a-link.md)\n```\n"
    edges = extract_markdown(txt, "x.md")
    assert not any("not-a-heading" in e["o"] or "a-link.md" in e["o"] for e in edges)

def test_slugify_matches_github_style():
    assert slugify("Sub-finding & Cases (2026)") == "sub-finding--cases-2026"
