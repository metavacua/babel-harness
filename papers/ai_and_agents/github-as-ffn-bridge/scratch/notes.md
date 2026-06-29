# Session Notes and Open Questions

## Provenance

- Session: babel-harness feature/github-as-ffn-bridge
- Commits: 94485d4 (pinned babel-harness), 4a120baf (pinned chrishayuk/larql)
- Key implementation: `scripts/build_repo_patch.py` (_INNER_SCRIPT, slot pre-scan fix)
- Tests: 33/33 passing as of commit a9a6e02

## Architecture Diagram (ASCII)

```
GitHub repo
    │
    ▼
extract-graph.py / github_graph.py
    │ (static analysis: calls, sources, sets_env, reads_env, …)
    ▼
Vindexfile   ←──── Form 1: INSERT "e","r","t"
    │
    ▼
build_repo_patch.py
    │ (gate vec = embed(e)×0.7 + cluster_centre(r)×0.3, normalised)
    │ (pre-scan eviction order — workaround for mmap/heap split bug)
    ▼
.vlp patch file  (op:"insert", WALK-visible via overrides_gate)
    │
    ▼  APPLY PATCH
larql vindex (in-memory overlay)
    │
    ▼  WALK "entity" TOP k
(layer, feature, gate_score, target_token)  ◄── AUDIT TRAIL
    │
    ▼
coding-agent / github_lql_bridge.py
```

## Citation Gaps

- No citation for the larql-to-sparql issue #242 (internal GitHub issue, not published)
- No citation for smollm2-360m itself — need HuggingFace model card reference
- Hopfield 1982 is a reasonable foundation but modern Hopfield networks
  (Ramsauer et al. 2020) may be more directly relevant to the capacity analysis

## Open Questions

1. **Capacity**: How many triples can be inserted into a single layer before
   retrieval precision degrades? Layer 19 has 2,560 slots; we use 95 (3.7%).
   What is the Hopfield capacity limit for this gate-KNN configuration?

2. **Cross-retrieval**: Do two inserts with similar entity embeddings activate
   each other's features? Need a test with deliberately close embeddings.

3. **Theory A vs B divergence**: No empirical measurements yet. The bridge
   server logs divergence but has not been run on a real repo query workload.

4. **Multi-token entities**: "pi-harness" tokenises to ["pi", "-", "harness"];
   WALK uses the final subword embedding ("arness"). Top-5 shows gate=+12.2
   but the display token may be confusing. Document the tokenisation convention.

5. **smollm2-360m specificity**: All findings about layer saturation and
   typical_layer() return values are specific to this model. A larger model
   (e.g. Gemma-3 4B) may have different layer profiles and COMPOSE may work.

## Potential Follow-ups

- Oracle test harness (Plan B): A×B×C evaluation of coding-agent reasoning
  vs. actual repo behaviour
- ternary-weight-encoder: adjacency CSV → Vindexfile INSERT triples
  (next pipeline step after graph extraction)
- Fixture commit (Task 4): tests/fixtures/babel-harness-94485d4.vlp
