## Summary

- DocBook 5.2 XML scholarly review of the `research-development-loop` orchestrator skill's first iteration, per the skill's honesty mandate
- Applies the skill's own self-evaluation: reports defects as prominently as improvements
- This PR **is** Phase 5 of the R&D loop that was never automatically invoked — it ran only after explicit user correction

## Key Findings

| Finding                                                                               | Status                 |
| ------------------------------------------------------------------------------------- | ---------------------- |
| F1: Loop abandonment — Phase 5/6 never reached automatically                          | confirmed              |
| F2: `bin/coding-agent` invoked 0 times despite explicit mandate (14% compliance rate) | confirmed              |
| F3: Scope guard achieves 2.25× speed improvement on mechanical tasks                  | confirmed-with-caveats |
| F4: Eval 1 assertion design mismatch — false −17pp regression in benchmark            | confirmed              |
| F5: Dependency analysis works; D-entry format lacks blocking/validation distinction   | confirmed-with-caveats |
| F6: Anomaly routing stable and identical across both skill versions                   | confirmed              |

## Compliance Tracking (iteration 1)

| Required sub-skill                           | Times invoked |
| -------------------------------------------- | ------------- |
| `superpowers:brainstorming`                  | 0             |
| `superpowers:writing-plans`                  | 0             |
| `bin/coding-agent`                           | 0             |
| `superpowers:test-driven-development`        | 0             |
| `superpowers:verification-before-completion` | 0             |
| `scholarly-white-paper`                      | 1 (late)      |
| `superpowers:finishing-a-development-branch` | 0             |

## Files

- `src/00-metadata.xml` — Dublin Core + Schema.org metadata
- `src/01-rdl-skill-evaluation.xml` — Primary article (6 findings, 10 sections, 5 references)
- `src/bibliography.bib` — BibTeX (Carnielli LFI, Logic of Evidence, Anthropic Agent Skills, Geva FFN)
- `xsl/html5.xsl` + `xsl/latex.xsl` — XSLT 1.0 transforms
- `schema/custom.rnc` — RELAX NG for finding sections
- `scratch/formulas.md` — Benchmark data + compliance table
- `scratch/notes.md` — Session provenance, open questions, architecture diagram

## Proposed Improvements for Iteration 2

1. Phase completion checklist (artifact existence gate, not sub-skill invocation)
2. Scope guard criterion 2 refinement: add 5th question about concrete value completeness
3. Eval 1 prompt replacement with a genuinely research-grade task
4. Eval 4: Artifact (A) category test
5. Compliance tracking assertion (zero invocations = orchestrator not functioning)
6. larql delegation scope clarification (coding artifacts vs. meta-files)

🤖 Generated with [Claude Code](https://claude.com/claude-code)🤖

### New commits since PR creation:

- da58942: --remote integration test
- f877d33: pre-compute fixture 95 insert ops
- afe132a: wrong importorskip fix — since reverted
- 3939e26: revert importorskip, document uv run pytest as canonical runner
- b8726b7: fix graph_vindex n<=1 early exit, eliminates scipy RuntimeWarning
- 95020bf: commit hook-pi-harness + ignore **pycache** and .pi-lens
