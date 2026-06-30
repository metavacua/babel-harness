# Orchestration Defects and Residual-Stream Improvements in the research-development-loop Skill

Scholarly white paper (DocBook 5.2 XML) evaluating the first iteration of improvements
to the `research-development-loop` orchestrator skill.

## Key Findings

- **F1 (confirmed):** Loop abandonment — Phase 5 (Scholarly Review) and Phase 6 (Finishing)
  were never reached in the evaluated session. `skill-creator` captured control after Phase 0
  without returning it. This paper is Phase 5 executing late after explicit user correction.
- **F2 (confirmed):** larql coding subagent (`bin/coding-agent`) invoked zero times despite
  explicit delegation requirement. Compliance rate: 1/7 required skill invocations (14%).
- **F3 (confirmed-with-caveats):** Scope guard achieves 2.25× speed improvement on mechanical
  tasks (129s vs 291s, eval 1) but misclassified "add --verbose flag" as fully-specified when
  the required step-name labels were absent.
- **F4 (confirmed):** Eval 1 assertion design mismatch — 2 assertions penalize correct scope
  guard behavior, producing a false −17pp pass rate regression.
- **F5 (confirmed-with-caveats):** Dependency analysis works correctly via code-path tracing,
  but both skill versions reached opposite task-ordering recommendations — both defensible,
  indicating a gap in the D entry format (blocking vs. validation dependencies not distinguished).
- **F6 (confirmed):** Anomaly routing to `superpowers:systematic-debugging` is stable and
  identical across both skill versions (4 tool calls each, all 4 assertions pass).

## Session Compliance Tracking

| Required sub-skill | Times invoked |
|--------------------|---------------|
| `superpowers:brainstorming` | 0 |
| `superpowers:writing-plans` | 0 |
| `bin/coding-agent` | 0 |
| `superpowers:test-driven-development` | 0 |
| `superpowers:verification-before-completion` | 0 |
| `scholarly-white-paper` | 1 (late) |
| `superpowers:finishing-a-development-branch` | 0 |

## Build

Requirements: `xsltproc` (libxslt), `xmllint` (libxml2).

```bash
make html     # generate generated/01-rdl-skill-evaluation.html
make latex    # generate generated/01-rdl-skill-evaluation.tex
make validate # validate XML against DocBook 5.2 schema (requires network)
```

## Structure

```
src/
  00-metadata.xml                Dublin Core + Schema.org metadata
  01-rdl-skill-evaluation.xml    Primary article (DocBook 5.2)
  bibliography.bib               BibTeX (5 references)
xsl/
  html5.xsl                      DocBook → HTML5
  latex.xsl                      DocBook → LaTeX
schema/
  custom.rnc                     RELAX NG for finding sections
scratch/
  formulas.md                    Benchmark data and compliance table
  notes.md                       Session provenance and open questions
```
