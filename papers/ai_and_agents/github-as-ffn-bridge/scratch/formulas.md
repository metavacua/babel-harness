# Formulas and Reference Tables

## Gate Vector Synthesis

```
gate_vec = normalise(embed(entity) × 0.7 + cluster_centre(relation) × 0.3)
         × avg_L2_norm(gate_vectors[layer, 0:100])
```

Source: `larql-python/src/vindex.rs:971–1007`

## Layer Assignment — Theory A (Topological)

```
l(v) = min(floor(d(v, seed) / D × num_layers), num_layers - 1)
l(r) = round(mean((l(u) + l(v)) / 2  for (u, r, v) in E_r))
```

Where:
- `d(v, seed)` = BFS hop-distance from seed to v in the undirected code graph
- `D` = max BFS distance from seed to any reachable node
- Unreachable nodes: fallback = `num_layers // 2`

## Layer Assignment — Theory B (Spectral)

```
L_norm = D^{-1/2} A D^{-1/2}   (normalised graph Laplacian, undirected)
[λ_0, λ_1, …, λ_{k-1}], [x_0, x_1, …, x_{k-1}] = eigsh(L_norm, k=num_layers+1, which="SM")

B_l = {λ_i : λ_min + l*(λ_max-λ_min)/num_layers ≤ λ_i < λ_min + (l+1)*(λ_max-λ_min)/num_layers}

l(v) = argmax_l  Σ_{λ_i ∈ B_l} x_v(i)^2
l(r) = round(mean((l(u) + l(v)) / 2  for (u, r, v) in E_r))
```

Notes:
- Trivial eigenvalue λ_0 = 0 (constant eigenvector) is excluded from band assignment
- `k = min(num_layers + 1, n - 1)` (eigsh requires k < N)
- On N=1 (single-node graph): eigsh falls back to scipy.linalg.eigh; all eigenvalues = 0;
  node gets layer 0

## smollm2-360m Layer 19 Statistics

| Property | Value |
|---|---|
| Total feature slots | 2,560 |
| Free slots | 0 (100% occupied) |
| Weakest feature | F2024, top='Tro', c_score=1.2936 |
| 95-insert blast radius | 95 weakest base features evicted in overlay |

## Patch Format: down_meta Serde Keys

Rust `PatchDownMeta` uses `#[serde(rename)]`:

| Rust field | JSON key |
|---|---|
| `top_token` | `"t"` |
| `top_token_id` | `"i"` |
| `c_score` | `"c"` |

Without these compact keys, `APPLY PATCH` defaults `top_token_id=0` — the same
stub defect as Vindexfile Form 1 INSERT per larql-to-sparql issue #242.

## Verified WALK Output (pi-harness, 95-triple patch applied)

```
WALK "pi-harness" TOP 5 →
  L19:F306  gate=+12.2  top="curl"
  L19:F293  gate=+12.2  top="("         ← base feature (eviction candidate)
  L19:F2320 gate=+12.2  top="OPENROUTER_CHECK_URL"
  ...
```

Gate score +12.2 confirms the gate vectors are correctly synthesised and
normalised to layer-19 magnitude.
