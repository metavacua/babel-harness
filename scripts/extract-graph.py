#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
babel-harness graphify extractor — larql-graph-extractor skill implementation.

Parses the bash codebase into a knowledge graph using the graphify approach:
  nodes: files, functions, env seams, external binaries, test suites, mocks
  edges: sources, calls, invokes, mocks, tests, sets_env, reads_env, implements, is_a

Then applies ternary-weight-encoder (D^{-1/2} A D^{-1/2} → I2_S trits) and
emits a Vindexfile with INSERT triples representing the +1 trit edges.
"""

import json
import os
import re
import sys
import math
from pathlib import Path

REPO = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Phase 1: graph extraction (larql-graph-extractor skill)
# ---------------------------------------------------------------------------

def extract_functions(src: str) -> list[str]:
    """Return function names defined in a bash source string."""
    funcs = []
    # Matches both `foo() {` and `function foo {` (parens optional in bash)
    for m in re.finditer(r'^(?:function\s+)?([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\(\s*\))?\s*\{', src, re.M):
        name = m.group(1)
        if name not in ('if', 'while', 'for', 'until', 'case', 'select'):
            funcs.append(name)
    return funcs

def extract_sources(src: str, file_path: Path) -> list[str]:
    """Return paths sourced by a bash file, relative to repo root."""
    results = []
    for m in re.finditer(r'(?:source|\.)\s+"?([^"\s]+)"?', src, re.M):
        raw = m.group(1)
        # Resolve $(...) and ${...} expansions that are simple path references
        raw = re.sub(r'\$\([^)]+\)', '', raw)  # strip $(...) — too dynamic
        raw = re.sub(r'\$\{[^}]+\}', '', raw)
        raw = raw.strip('/')
        if raw and not raw.startswith('$'):
            # Normalize: strip leading ../ relative to bin/ scripts
            p = Path(raw)
            results.append(str(p.name) if '/' not in raw else raw.replace('../', ''))
    return results

def extract_seams(src: str) -> list[str]:
    """Return testable seam variable names (VAR="${VAR:-default}" pattern)."""
    seams = []
    for m in re.finditer(r'^([A-Z_][A-Z0-9_]+)="\$\{\1:-', src, re.M):
        seams.append(m.group(1))
    return seams

def extract_env_reads(src: str, known_seams: set[str]) -> list[str]:
    """Return seam variables READ (not defined) in a file."""
    reads = []
    for m in re.finditer(r'\$\{?([A-Z_][A-Z0-9_]+)', src):
        v = m.group(1)
        if v in known_seams:
            reads.append(v)
    return reads

def extract_external_invocations(src: str) -> list[str]:
    """Return external binary names invoked (goose, larql, curl, etc.)."""
    binaries = set()
    for pattern in [
        r'"?\$(?:GOOSE_BIN|LARQL_BIN|PI_BIN)"',
        r'\btimeout\s+["\d$]+\s+"?\$(?:GOOSE_BIN|LARQL_BIN)"',
    ]:
        for m in re.finditer(pattern, src):
            binaries.add(m.group(0).split('_BIN')[0].lstrip('"$'))
    # Also pick up bare invocations of known externals
    for ext in ('goose', 'larql', 'curl', 'sudo', 'timeout', 'ss', 'awk', 'wait', 'pgrep'):
        if re.search(rf'\b{ext}\b', src):
            binaries.add(ext)
    return list(binaries)

def extract_function_calls(func_body: str, known_funcs: set[str]) -> list[str]:
    """Return known internal functions called within a function body."""
    calls = []
    for fn in known_funcs:
        if re.search(rf'\b{re.escape(fn)}\b', func_body):
            calls.append(fn)
    return calls

def parse_file(path: Path, all_funcs: set[str], all_seams: set[str]) -> dict:
    """Parse one bash file and return its graph contribution."""
    src = path.read_text()
    rel = str(path.relative_to(REPO))
    node_id = path.stem if path.stem not in ('', '.') else path.name

    funcs = extract_functions(src)
    sources = extract_sources(src, path)
    seams = extract_seams(src)
    env_reads = list(set(extract_env_reads(src, all_seams)) - set(seams))
    externals = extract_external_invocations(src)

    return {
        'id': node_id,
        'path': rel,
        'type': 'test_suite' if 'test' in node_id else (
                  'mock' if 'mocks' in rel else 'script'),
        'functions': funcs,
        'sources': sources,
        'seams': seams,
        'env_reads': env_reads,
        'externals': externals,
    }

def build_graph(repo: Path) -> tuple[list[str], list[tuple]]:
    """
    Build (entities, edges) from the babel-harness codebase.
    edges: list of (from_entity, relation, to_entity, confidence)
    """
    bash_files = (
        list((repo / 'bin').glob('*')) +
        list((repo / 'lib').glob('*.sh')) +
        list((repo / 'tests').rglob('*.bash')) +
        list((repo / 'tests' / 'mocks').glob('*'))
    )
    bash_files = [f for f in bash_files if f.is_file()]

    # Two-pass: collect all functions and seams globally first
    all_funcs: set[str] = set()
    all_seams: set[str] = set()
    for f in bash_files:
        src = f.read_text()
        all_funcs.update(extract_functions(src))
        all_seams.update(extract_seams(src))

    # External binaries that are mocked
    mock_targets = {'goose', 'larql', 'curl'}

    parsed = [parse_file(f, all_funcs, all_seams) for f in bash_files]
    file_ids = {p['id'] for p in parsed}

    entities: list[str] = []
    edges: list[tuple] = []

    def add_entity(eid: str):
        if eid not in entities:
            entities.append(eid)

    def add_edge(frm: str, rel: str, to: str, conf: float = 1.0):
        add_entity(frm)
        add_entity(to)
        edges.append((frm, rel, to, conf))

    # Add file nodes
    for p in parsed:
        add_entity(p['id'])

    # Add function nodes
    for fn in sorted(all_funcs):
        add_entity(fn)

    # Add seam nodes
    for s in sorted(all_seams):
        add_entity(s)

    # Add external binary nodes
    for ext in sorted({'goose', 'larql', 'curl', 'timeout', 'ss', 'awk', 'pgrep', 'sudo'}):
        add_entity(ext)

    # Add type edges
    add_edge('coding-agent', 'is_a', 'script')
    add_edge('pi-harness', 'is_a', 'script')
    add_edge('harness-common', 'is_a', 'library')
    add_edge('test-coding-agent', 'is_a', 'test_suite')
    add_edge('test-pi-harness', 'is_a', 'test_suite')
    add_edge('goose', 'is_a', 'external_binary')
    add_edge('larql', 'is_a', 'external_binary')
    add_edge('curl', 'is_a', 'external_binary')

    for p in parsed:
        fid = p['id']

        # source edges
        for s in p['sources']:
            # Normalize library name
            s_clean = Path(s).stem
            if s_clean in file_ids or s_clean == 'harness-common':
                add_edge(fid, 'sources', s_clean)

        # seam definitions
        for seam in p['seams']:
            add_edge(fid, 'sets_env', seam)

        # seam reads
        for seam in p['env_reads']:
            add_edge(fid, 'reads_env', seam, conf=0.7)

        # mock edges
        if p['type'] == 'mock':
            mock_target = p['id']
            if mock_target in mock_targets:
                add_edge(fid, 'mocks', mock_target)

        # test edges (test suites test the scripts)
        if p['type'] == 'test_suite':
            if 'coding-agent' in fid:
                add_edge(fid, 'tests', 'coding-agent')
            elif 'pi-harness' in fid:
                add_edge(fid, 'tests', 'pi-harness')

        # external invocations
        for ext in p['externals']:
            if ext in ('goose', 'larql', 'curl', 'timeout', 'ss', 'awk', 'pgrep', 'sudo'):
                add_edge(fid, 'invokes', ext, conf=0.8)

    # Function-level edges (which file defines which function)
    func_owners: dict[str, str] = {}
    for p in parsed:
        for fn in p['functions']:
            func_owners[fn] = p['id']
            add_edge(p['id'], 'defines', fn)

    # Cross-function call edges (within same file approximation based on known topology)
    # These are the key structural relationships in babel-harness
    known_calls = [
        ('coding-agent', 'calls', '_check_openrouter'),
        ('coding-agent', 'calls', '_check_larql'),
        ('coding-agent', 'calls', '_start_larql_server'),
        ('coding-agent', 'calls', '_run_goose_openrouter'),
        ('coding-agent', 'calls', '_run_goose_larql'),
        ('_start_larql_server', 'calls', '_check_larql'),
        ('_start_larql_server', 'calls', '_larql_find_server_pid'),
        ('_start_larql_server', 'calls', '_enroll_larql_in_cgroup'),
        ('_run_goose_openrouter', 'calls', '_run_goose_call'),
        ('_run_goose_larql', 'calls', '_run_goose_call'),
        ('_larql_cleanup', 'calls', '_LARQL_SERVER_PID'),
        ('pi-harness', 'calls', '_check_openrouter'),
        ('pi-harness', 'calls', '_check_ollama'),
        ('pi-harness', 'calls', '_setup_cgroup'),
        ('pi-harness', 'calls', '_enroll_ollama_in_cgroup'),
        ('pi-harness', 'calls', '_warmup_ollama_model'),
        ('pi-harness', 'calls', '_select_provider_model'),
        ('test-coding-agent', 'invokes', 'coding-agent'),
        ('test-pi-harness', 'invokes', 'pi-harness'),
        ('test-coding-agent', 'uses_mock', 'goose'),
        ('test-coding-agent', 'uses_mock', 'larql'),
        ('test-coding-agent', 'uses_mock', 'curl'),
        ('test-pi-harness', 'uses_mock', 'curl'),
    ]
    for frm, rel, to in known_calls:
        add_edge(frm, rel, to)

    # Architectural implements edges
    arch_edges = [
        ('coding-agent', 'implements', 'openrouter-provider'),
        ('coding-agent', 'implements', 'larql-provider'),
        ('coding-agent', 'implements', 'provider-routing'),
        ('coding-agent', 'implements', 'larql-server-lifecycle'),
        ('coding-agent', 'implements', 'goose-error-detection'),
        ('pi-harness', 'implements', 'openrouter-provider'),
        ('pi-harness', 'implements', 'ollama-provider'),
        ('pi-harness', 'implements', 'cgroup-enforcement'),
        ('harness-common', 'implements', 'openrouter-health-check'),
        ('_run_goose_call', 'implements', 'rate-limit-detection'),
        ('_enroll_larql_in_cgroup', 'implements', 'cgroup-enrollment'),
        ('_start_larql_server', 'implements', 'server-startup-poll'),
        ('_start_larql_server', 'implements', 'fast-fail-on-launcher-error'),
    ]
    for frm, rel, to in arch_edges:
        add_edge(frm, rel, to)

    return entities, edges


# ---------------------------------------------------------------------------
# Phase 2: ternary weight encoder (ternary-weight-encoder skill)
# ---------------------------------------------------------------------------

def ternary_encode(entities: list[str], edges: list[tuple],
                   theta: float | None = None) -> list[tuple]:
    """
    Apply D^{-1/2} A D^{-1/2} normalization then I2_S trit encode.
    Returns list of (entity_i, relation, entity_j, trit) for trit != 0.

    theta: adaptive threshold = 0.5 * mean(|Ã[i,j]| > 0)
           Pass None to use adaptive; pass 0.0 to emit all non-zero edges as +1.
    """
    n = len(entities)
    idx = {e: i for i, e in enumerate(entities)}

    # Build adjacency matrix (confidence-weighted, directed)
    A = [[0.0] * n for _ in range(n)]
    edge_relations: dict[tuple[int,int], str] = {}
    for frm, rel, to, conf in edges:
        i, j = idx.get(frm, -1), idx.get(to, -1)
        if i < 0 or j < 0:
            continue
        if A[i][j] < conf:
            A[i][j] = conf
            edge_relations[(i, j)] = rel

    # Symmetrize: A_sym = (A + A^T) / 2
    A_sym = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            A_sym[i][j] = (A[i][j] + A[j][i]) / 2.0

    # Degree matrix (diagonal)
    D_inv_sqrt = [0.0] * n
    for i in range(n):
        deg = sum(A_sym[i])
        D_inv_sqrt[i] = 1.0 / math.sqrt(deg) if deg > 0 else 0.0

    # Normalized adjacency: Ã = D^{-1/2} A_sym D^{-1/2}
    A_norm = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            A_norm[i][j] = D_inv_sqrt[i] * A_sym[i][j] * D_inv_sqrt[j]

    # Adaptive threshold: 0.5 × mean of non-zero |Ã| values
    nonzero_vals = [abs(A_norm[i][j]) for i in range(n) for j in range(n)
                    if A_norm[i][j] != 0.0]
    if theta is None:
        theta = 0.5 * (sum(nonzero_vals) / len(nonzero_vals)) if nonzero_vals else 0.0

    # Trit encode — only emit ORIGINAL directed edges that survive the threshold.
    # Symmetrization is used only to compute the importance score (Ã[i,j]).
    # We never emit the reverse of an edge that wasn't in the original graph,
    # which prevents e.g. "function defines file" from appearing when the
    # original was "file defines function".
    trits = []
    for (i, j), rel in edge_relations.items():
        v = A_norm[i][j]  # normalized weight for this original edge direction
        if v > theta:
            trit = 1
        elif v < -theta:
            trit = -1
        else:
            continue
        trits.append((entities[i], rel, entities[j], trit))

    return trits


# ---------------------------------------------------------------------------
# Phase 3: Vindexfile generation
# ---------------------------------------------------------------------------

_VINDEX_BASE = os.environ.get('LARQL_VINDEX_BASE') or (
    sys.exit("error: LARQL_VINDEX_BASE env var is required\n"
             "  e.g. LARQL_VINDEX_BASE=/path/to/larql-vindexes python3 scripts/extract-graph.py")
    or ""
)
VINDEXFILE_HEADER = f"""\
# Re-generate: LARQL_VINDEX_BASE=/path/to/larql-vindexes python3 scripts/extract-graph.py
FROM {_VINDEX_BASE}/smollm2-360m.vindex

# babel-harness coding subagent — generated by scripts/extract-graph.py
# BitNet I2_S ternary encoding of graphify knowledge graph.
# Skills used: larql-graph-extractor → ternary-weight-encoder → larql build
#
# Pipeline: bash AST extraction → D^{{-½}}AD^{{-½}} normalization
#           → I2_S ternary encode → sparse INSERT triples (this file)
# Base model: smollm2-360m (language understanding)
# Overlay: babel-harness graph knowledge (coding subagent retrieval)

EXPOSE browse
"""

def write_vindexfile(trits: list[tuple], entities: list[str], edges: list[tuple],
                     out: Path) -> None:
    """Write Vindexfile with header + INSERT triples for +1 trit edges."""
    lines = [VINDEXFILE_HEADER]

    # Group by relation for readability
    by_rel: dict[str, list[tuple]] = {}
    for frm, rel, to, trit in trits:
        if trit == 1:  # Only +1 trits become INSERT statements
            by_rel.setdefault(rel, []).append((frm, to))

    for rel in sorted(by_rel):
        lines.append(f"\n# {rel}")
        for frm, to in sorted(by_rel[rel]):
            lines.append(f'INSERT "{frm}", "{rel}", "{to}"')

    out.write_text('\n'.join(lines) + '\n')
    print(f"[graph] Vindexfile written to {out}", file=sys.stderr)


def write_larql_json(edges: list[tuple], out: Path) -> None:
    """Write larql.json format for use with `larql describe --graph`."""
    graph = {
        "larql_version": "1.0",
        "metadata": {},
        "schema": {},
        "edges": [{"s": f, "r": r, "o": t, "c": c} for f, r, t, c in edges],
    }
    out.write_text(json.dumps(graph, indent=2) + '\n')
    print(f"[graph] larql.json written to {out} ({len(edges)} edges)", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    repo = REPO
    out_dir = repo / 'graphify-out'
    out_dir.mkdir(exist_ok=True)

    print("[graph] Phase 1: extracting babel-harness knowledge graph...", file=sys.stderr)
    entities, edges = build_graph(repo)
    print(f"[graph]   {len(entities)} nodes, {len(edges)} edges", file=sys.stderr)

    write_larql_json(edges, out_dir / 'babel-harness.larql.json')

    # Also write human-readable edge list
    edge_list = out_dir / 'edges.tsv'
    edge_list.write_text(
        'from\trelation\tto\tconfidence\n' +
        '\n'.join(f'{f}\t{r}\t{t}\t{c}' for f, r, t, c in sorted(edges))
    )

    print("[graph] Phase 2: ternary weight encoding (I2_S, θ=adaptive)...", file=sys.stderr)
    trits = ternary_encode(entities, edges)
    pos = sum(1 for *_, t in trits if t == 1)
    neg = sum(1 for *_, t in trits if t == -1)
    print(f"[graph]   {len(trits)} non-zero trits: +1={pos}, -1={neg}", file=sys.stderr)

    # Write trit table
    trit_file = out_dir / 'trits.tsv'
    trit_file.write_text(
        'from\trelation\tto\ttrit\n' +
        '\n'.join(f'{f}\t{r}\t{t}\t{v}' for f, r, t, v in sorted(trits))
    )

    print("[graph] Phase 3: generating Vindexfile...", file=sys.stderr)
    write_vindexfile(trits, entities, edges, repo / 'Vindexfile')

    print("[graph] Done. Next: larql build", file=sys.stderr)
    print(f"""
Summary
-------
Nodes:       {len(entities)}
Edges:       {len(edges)}
+1 trits:    {pos}  (→ INSERT statements in Vindexfile)
-1 trits:    {neg}  (inhibiting relationships, not in Vindexfile)

Output files:
  graphify-out/babel-harness.larql.json  Queryable graph (larql describe --graph)
  graphify-out/edges.tsv                 Human-readable edge list
  graphify-out/trits.tsv                 Ternary weight matrix
  Vindexfile                             larql build input

Next steps:
  larql build ./ -o build/babel-harness-coding-agent.vindex
  larql link build/babel-harness-coding-agent.vindex
  larql describe "coding-agent"
""")


if __name__ == '__main__':
    main()
