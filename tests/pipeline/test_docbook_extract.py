# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.docbook_extract import extract_docbook, extract_bib

DOCBOOK = """<?xml version="1.0"?>
<article xmlns="http://docbook.org/ns/docbook" xmlns:xlink="http://www.w3.org/1999/xlink">
  <title>LLM Database Theory</title>
  <section><title>The Position</title>
    <para>See <citation>hay2024larql</citation> and
      <link xlink:href="https://github.com/chrishayuk/larql"/>.</para>
    <section><title>Nested</title><para>x</para></section>
  </section>
</article>"""

DOCBOOK_EMPHASIS_TITLE = """<?xml version="1.0"?>
<article xmlns="http://docbook.org/ns/docbook" xmlns:xlink="http://www.w3.org/1999/xlink">
  <title>LLM <emphasis>Database</emphasis> Theory</title>
  <section><title>The <emphasis>Real</emphasis> Position</title>
    <para>x</para>
  </section>
</article>"""

BIB = """@misc{hay2024larql,
  title = {LARQL: the model is the database},
  author = {Hay, Chris},
}
@article{geva2021,
  title        = {Transformer FFN Layers Are Key-Value Memories},
}"""

# Mirrors the real corpus entry (papers/ai_and_ip/llm-database-theory/src/bibliography.bib):
# a title with nested braces that a naive "stop at first }" regex truncates.
BIB_NESTED_BRACES = """@misc{hay2024larql,
  author       = {Hay, Chris},
  title        = {{LARQL} --- {Lazarus Query Language}: A Graph Database Interface to Transformer Language Model Weights},
  year         = {2024},
}"""

# booktitle appears before title; extraction must not pick up booktitle's value.
BIB_BOOKTITLE_BEFORE_TITLE = """@inproceedings{carlini2023extracting,
  author       = {Carlini, Nicholas},
  booktitle    = {32nd {USENIX} Security Symposium ({USENIX} Security 23)},
  title        = {Extracting Training Data from Diffusion Models},
  year         = {2023},
}"""

def test_docbook_sections_nest():
    edges = extract_docbook(DOCBOOK, "papers/x/src/01.xml")
    assert any(e["r"] == "contains" and e["s"] == "papers/x/src/01.xml"
               and e["o"] == "papers/x/src/01.xml#the-position" for e in edges)
    assert any(e["r"] == "contains" and e["s"] == "papers/x/src/01.xml#the-position"
               and e["o"] == "papers/x/src/01.xml#nested" for e in edges)

def test_docbook_citation_edge():
    edges = extract_docbook(DOCBOOK, "papers/x/src/01.xml")
    assert any(e["r"] == "cites" and e["o"] == "cite:hay2024larql" for e in edges)

def test_docbook_title():
    edges = extract_docbook(DOCBOOK, "papers/x/src/01.xml")
    assert any(e["r"] == "has-title" and e["o"] == "LLM Database Theory" for e in edges)

def test_bib_keys_and_titles():
    edges = extract_bib(BIB, "papers/x/src/bibliography.bib")
    assert {"s": "cite:hay2024larql", "r": "has-title",
            "o": "LARQL: the model is the database", "c": 1.0,
            "prov": "papers/x/src/bibliography.bib:1"} in edges
    assert any(e["s"] == "cite:geva2021" for e in edges)

def test_bib_title_with_nested_braces_full_text():
    edges = extract_bib(BIB_NESTED_BRACES, "papers/x/src/bibliography.bib")
    titles = [e["o"] for e in edges if e["s"] == "cite:hay2024larql" and e["r"] == "has-title"]
    assert titles == [
        "{LARQL} --- {Lazarus Query Language}: A Graph Database Interface to "
        "Transformer Language Model Weights"
    ]

def test_bib_title_not_confused_by_preceding_booktitle():
    edges = extract_bib(BIB_BOOKTITLE_BEFORE_TITLE, "papers/x/src/bibliography.bib")
    titles = [e["o"] for e in edges
              if e["s"] == "cite:carlini2023extracting" and e["r"] == "has-title"]
    assert titles == ["Extracting Training Data from Diffusion Models"]

def test_docbook_title_with_inline_markup():
    edges = extract_docbook(DOCBOOK_EMPHASIS_TITLE, "papers/x/src/01.xml")
    assert any(e["r"] == "has-title" and e["o"] == "LLM Database Theory"
               for e in edges)
    assert any(e["r"] == "contains"
               and e["o"] == "papers/x/src/01.xml#the-real-position"
               for e in edges)
