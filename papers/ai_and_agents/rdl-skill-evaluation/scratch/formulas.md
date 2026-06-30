# Benchmark Data — Iteration 1

## Per-eval timing and token counts

| Eval | Config | Tokens | Time (s) | Tool calls | Pass rate |
|------|--------|--------|----------|------------|-----------|
| 1 (verbose flag) | with_skill | 38,783 | 129 | ~10 | 2/4 (50%) |
| 1 (verbose flag) | old_skill | 48,914 | 291 | ~20 | 4/4 (100%) |
| 2 (task ordering) | with_skill | 75,218 | 282 | ~30 | 4/4 (100%) |
| 2 (task ordering) | old_skill | 66,044 | 389 | ~44 | 4/4 (100%) |
| 3 (OOM anomaly) | with_skill | 24,418 | 110 | 4 | 4/4 (100%) |
| 3 (OOM anomaly) | old_skill | 22,609 | 91 | 4 | 4/4 (100%) |

## Aggregate

| Metric | with_skill | old_skill | Delta |
|--------|-----------|-----------|-------|
| Pass rate | 83.3% (10/12) | 100% (12/12) | −17 pp |
| Avg time | 173.7s | 257.0s | −83.3s (−32%) |
| Avg tokens | 46,140 | 45,856 | +284 (~0%) |

## Speed improvement breakdown (eval 1 only)

- Scope guard skipped: steps 0.2, 0.4, 0.5, 0.6, 0.7
- Old skill ran: all 8 steps (0.1–0.8) including web search, issue check, dependency analysis, graph context, proposition evaluation
- New skill ran: steps 0.0, 0.1, 0.3, 0.8 only
- Time ratio: 291s / 129s = 2.25×

## Compliance tracking

| Required skill | Times invoked |
|---------------|---------------|
| superpowers:brainstorming | 0 |
| superpowers:writing-plans | 0 |
| bin/coding-agent | 0 |
| superpowers:test-driven-development | 0 |
| superpowers:verification-before-completion | 0 |
| scholarly-white-paper | 1 (late) |
| superpowers:finishing-a-development-branch | 0 |

Overall compliance rate: 1/7 = 14%
