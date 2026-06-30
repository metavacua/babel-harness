# GitHub Repository Graphs as Auditable FFN for Coding Agents

Scholarly white paper documenting the GitHub-as-FFN-bridge architecture:
using a repository's call graph, dependency graph, and environment-seam graph
as the feed-forward network layer of a language model, making coding agent
reasoning fully auditable.

## Key Findings

- **F1 (confirmed):** `VectorIndex.find_free_feature()` in larql-python has a
  mmap/heap split bug that causes all batch inserts to collide on the same
  `(layer, feature)` slot. Fixed by pre-scanning eviction order before any writes.
- **F2 (confirmed):** Layer 19 of smollm2-360m is 100% occupied (2,560/2,560 slots).
  95-triple inserts evict the 95 weakest base features in-overlay; disk is unchanged.
- **F3 (confirmed-with-caveats):** Gate vector synthesis replication in
  `build_repo_patch.py` mirrors `larql-python/src/vindex.rs:971–1007` with a minor
  normalization fidelity caveat for inserts into slots with index < 100.

## Build

Requirements: `xsltproc` (libxslt), `xmllint` (libxml2).
Optional: `jing` for RELAX NG validation, `pdflatex` for PDF.

```bash
make validate   # validate XML against DocBook 5.2 schema
make html       # generate generated/*.html
make latex      # generate generated/*.tex
make pdf        # generate generated/*.pdf (requires pdflatex)
make clean      # remove generated outputs
```

## Structure

```
src/
  00-metadata.xml     Dublin Core + Schema.org metadata (XIncluded by articles)
  01-github-as-ffn.xml  Primary article (DocBook 5.2)
  bibliography.bib    BibTeX sources (6 references)
xsl/
  html5.xsl           DocBook → HTML5 with DC meta tags + Schema.org JSON-LD
  latex.xsl           DocBook → LaTeX (article class, biblatex)
schema/
  custom.rnc          RELAX NG Compact: role="finding" + condition constraints
scratch/
  formulas.md         Gate vector formula, layer assignment equations, reference tables
  notes.md            Session provenance, architecture diagram, open questions
generated/            (git-ignored) HTML5 and LaTeX outputs
```

## Implementation

- `scripts/build_repo_patch.py` — Vindexfile Form 1 → .vlp patch (WALK-visible inserts)
- `scripts/github_lql_bridge.py` — FastAPI bridge: dual-theory WalkFfn divergence logging
- `scripts/graph_vindex.py` — topological + spectral layer assignment
- `Vindexfile` — 95 Form-1 INSERT directives for babel-harness@94485d4
