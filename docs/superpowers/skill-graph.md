# Superpowers Skill Graph — babel-harness coding subagent

## Node inventory

| Skill | Type | Role in this project |
|-------|------|---------------------|
| `brainstorming` | existing | Design approach: Vindexfile + larql build (approach A) |
| `larql-graph-extractor` | **NEW** | Extract bash codebase → knowledge graph adjacency |
| `ternary-weight-encoder` | **NEW** | D⁻½AD⁻½ normalization → I2_S ternary → INSERT triples |
| `writing-plans` | existing | Implementation plan for multi-task execution |
| `subagent-driven-development` | existing | Delegate tasks to implementer subagents with review |
| `test-driven-development` | existing | Red-green-refactor for each implementation task |
| `verification-before-completion` | existing | Verify vindex is queryable before marking done |
| `systematic-debugging` | existing | Debug larql build failures or vindex mismatches |

## Dependency edges (directed acyclic graph)

```
brainstorming ──────────────────────────────────────────┐
                                                         ↓
larql-graph-extractor ──→ (extraction pipeline) ──→ writing-plans
                                                         ↓
ternary-weight-encoder ──→ (INSERT triples)    ──→ subagent-driven-development
                                                    │
                   ┌────────────────────────────────┤
                   ↓                                ↓
          test-driven-development       verification-before-completion
                   │                                │
                   └──── systematic-debugging ──────┘
```

## Execution order

1. `brainstorming` → design decision (approach A: Vindexfile + smollm2-360m base)
2. `larql-graph-extractor` → `scripts/extract-graph.py` → graph of babel-harness
3. `ternary-weight-encoder` → normalized adjacency → INSERT triples in `Vindexfile`
4. `writing-plans` → `docs/superpowers/plans/2026-06-24-babel-harness-vindex.md`
5. `subagent-driven-development` → build pipeline tasks:
   - Task 1: graph extraction script (TDD)
   - Task 2: ternary encoder (TDD)
   - Task 3: Vindexfile generation
   - Task 4: larql build + link
   - Task 5: coding-agent integration
6. `verification-before-completion` → `larql describe "coding-agent"` confirms graph edges

## New skills produced

- `larql-graph-extractor`: general skill for any codebase → larql knowledge graph
- `ternary-weight-encoder`: general skill for graph → BitNet I2_S ternary weights

Both skills are reusable for other codebases beyond babel-harness.
