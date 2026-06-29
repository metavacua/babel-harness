#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
GitHub remote vindex extractor — treats any GitHub repo as a LARQL-queryable triple-store.

Formally: a GitHub repo G = (V, E) is isomorphic to a LARQL vindex entity graph.
This script extracts (V, E) via the GitHub API and outputs LQL INSERT triples.

DDT properties:
  Quasi-deterministic: same repo@ref → same triples given stable GitHub API; network I/O
                       is nondeterministic by nature (rate limits, flaky DNS, API changes)
  Decidable:           all loops bounded by finite file tree size
  Tractable:           O(|V| + |E|) graph extraction; O(|V| × avg_tokens) TF-IDF scoring
  No unhandled errors: all failure paths return typed JSON error body

Usage:
  python3 scripts/github_graph.py --repo chrishayuk/larql
  python3 scripts/github_graph.py --repo chrishayuk/larql --ref main --output lql
  python3 scripts/github_graph.py --repo chrishayuk/larql --query "server inference" --knn 5
  python3 scripts/github_graph.py --repo chrishayuk/larql --output json

Options:
  --repo OWNER/REPO   GitHub repository (required)
  --ref REF           Branch/tag/commit (default: main)
  --query TEXT        Task description for gate-KNN retrieval
  --knn K             Top-K nodes to return in query mode (default: 5)
  --hops N            BFS hops from seed nodes in query mode (default: 2)
  --max-nodes N       Maximum nodes in BFS walk (default: 20)
  --output FORMAT     Output format: lql (default), json, tsv
  --token-file PATH   File containing GitHub token (default: none, uses gh CLI)

Environment:
  GITHUB_TOKEN        GitHub API token (overrides --token-file)
  GH_TOKEN            Alternative GitHub API token
"""

import concurrent.futures
import json
import math
import os
import re
import subprocess
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Error ADT — all failure paths return one of these
# ---------------------------------------------------------------------------

class GraphError(Exception):
    """Typed error for DDT compliance. Always serialisable to JSON."""

    def __init__(self, kind: str, **fields):
        self.kind = kind
        self.fields = fields

    def to_json(self) -> str:
        return json.dumps({"ok": False, "error": self.kind, **self.fields})

    def __str__(self):
        return self.to_json()


def _err(kind: str, **fields) -> GraphError:
    return GraphError(kind, **fields)


# ---------------------------------------------------------------------------
# GitHub API client (read-only, no POST/PATCH/DELETE)
# ---------------------------------------------------------------------------

def _gh_api(endpoint: str, token: Optional[str] = None) -> dict | list:
    """
    GET a GitHub API endpoint. Returns parsed JSON.

    Uses `gh api` if available (respects ~/.config/gh/hosts.yml auth),
    otherwise falls back to curl with GITHUB_TOKEN / GH_TOKEN.

    Raises GraphError on all failure paths (no unhandled exceptions).
    """
    # Try gh CLI first (available and authenticated in this environment)
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        # gh failure: parse error message
        err_text = result.stderr.strip() or result.stdout.strip()
        if "404" in err_text:
            raise _err("NotFound", endpoint=endpoint)
        if "401" in err_text or "403" in err_text:
            raise _err("PermissionDenied", endpoint=endpoint)
        if "rate limit" in err_text.lower():
            raise _err("RateLimited", endpoint=endpoint)
        raise _err("APIError", endpoint=endpoint, cause=err_text[:200])
    except subprocess.TimeoutExpired:
        raise _err("Timeout", endpoint=endpoint, limit_secs=30)
    except FileNotFoundError:
        pass  # gh not available, fall through to curl

    # Fallback: curl with token
    tok = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    url = f"https://api.github.com/{endpoint.lstrip('/')}"
    # Build header args as pairs: ["-H", "Accept: ...", "-H", "Authorization: ..."]
    header_args: list[str] = ["-H", "Accept: application/vnd.github+json"]
    if tok:
        header_args += ["-H", f"Authorization: Bearer {tok}"]
    try:
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "30"] + header_args + [url],
            capture_output=True, text=True, timeout=35
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
        if result.returncode != 0:
            raise _err("NetworkUnreachable", url=url, cause=result.stderr[:200])
        raise _err("EmptyResponse", url=url)
    except subprocess.TimeoutExpired:
        raise _err("Timeout", url=url, limit_secs=30)
    except json.JSONDecodeError as e:
        raise _err("ParseError", url=url, msg=str(e))
    except FileNotFoundError:
        raise _err("ToolNotFound", cause="neither 'gh' nor 'curl' found in PATH")


def _fetch_tree(owner: str, repo: str, ref: str) -> list[dict]:
    """Fetch recursive file tree. Returns list of {path, type, size, sha}."""
    endpoint = f"repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
    try:
        data = _gh_api(endpoint)
    except GraphError:
        raise
    if not isinstance(data, dict) or "tree" not in data:
        raise _err("ParseError", endpoint=endpoint, msg="missing 'tree' key")
    if data.get("truncated", False):
        print(json.dumps({"ok": False, "warning": "TreeTruncated",
                          "msg": "GitHub truncated the recursive tree (repo too large); "
                                 "graph is partial — increase depth or use sparse checkout",
                          "repo": f"{owner}/{repo}", "ref": ref}),
              file=sys.stderr)
    return data["tree"]


def _fetch_content_raw(owner: str, repo: str, path: str, ref: str) -> str:
    """Fetch raw file content as a string. Raises GraphError on failure."""
    import base64
    endpoint = f"repos/{owner}/{repo}/contents/{path}?ref={ref}"
    try:
        data = _gh_api(endpoint)
    except GraphError:
        raise
    if not isinstance(data, dict) or "content" not in data:
        raise _err("ParseError", endpoint=endpoint, msg="missing 'content' key")
    try:
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except Exception as e:
        raise _err("ParseError", endpoint=endpoint, msg=str(e))


# ---------------------------------------------------------------------------
# Graph extraction
# ---------------------------------------------------------------------------

Language = str  # "rust", "python", "bash", "unknown"

def _detect_language(path: str) -> Language:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"rs": "rust", "py": "python", "sh": "bash",
            "ts": "typescript", "js": "javascript", "go": "go",
            "md": "markdown", "toml": "toml", "json": "json",
            "yaml": "yaml", "yml": "yaml"}.get(ext, "unknown")


def _parse_rust_imports(content: str) -> list[str]:
    """Extract 'use crate_name::...' and 'extern crate crate_name' from Rust source."""
    imports = []
    for m in re.finditer(r'^use\s+([\w:]+)', content, re.MULTILINE):
        top = m.group(1).split("::")[0]
        if top not in ("std", "core", "alloc", "super", "self", "crate"):
            imports.append(top)
    for m in re.finditer(r'^extern\s+crate\s+(\w+)', content, re.MULTILINE):
        imports.append(m.group(1))
    return list(set(imports))


def _parse_python_imports(content: str) -> list[str]:
    """Extract top-level module names from Python import statements."""
    imports = []
    for m in re.finditer(r'^(?:import|from)\s+([\w.]+)', content, re.MULTILINE):
        top = m.group(1).split(".")[0]
        imports.append(top)
    return list(set(imports))


def _parse_toml_dependencies(content: str) -> list[tuple[str, str]]:
    """Extract (name, version_spec) from Cargo.toml [dependencies] sections."""
    deps = []
    in_deps = False
    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r'^\[.*dependencies.*\]', stripped):
            in_deps = True
            continue
        if stripped.startswith("[") and not stripped.startswith("[dependencies"):
            in_deps = False
        if in_deps and "=" in stripped and not stripped.startswith("#"):
            name = stripped.split("=")[0].strip().replace("-", "_").lower()
            version = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            # Skip workspace = true style
            if not version.startswith("{") or '"version"' in version:
                ver_m = re.search(r'"([^"]+)"', version)
                deps.append((name, ver_m.group(1) if ver_m else "*"))
    return deps


def build_graph(owner: str, repo: str, ref: str,
                token: Optional[str] = None,
                max_content_files: int = 30) -> tuple[list[str], list[tuple]]:
    """
    Build (entities, edges) from a GitHub repository.

    entities: list of unique string IDs
    edges: list of (from_entity, relation, to_entity, confidence)

    Bounded: fetches at most max_content_files file contents (tractability).
    All error paths raise GraphError (no unhandled exceptions).
    """
    repo_id = f"{owner}/{repo}"
    entities: list[str] = []
    edges: list[tuple] = []
    seen_entities: set[str] = set()
    seen_edges: set[tuple] = set()

    def add_entity(eid: str):
        if eid not in seen_entities:
            seen_entities.add(eid)
            entities.append(eid)

    def add_edge(frm: str, rel: str, to: str, conf: float = 1.0):
        key = (frm, rel, to)
        if key not in seen_edges:
            seen_edges.add(key)
            add_entity(frm)
            add_entity(to)
            edges.append((frm, rel, to, conf))

    # Repo root node
    add_entity(repo_id)

    # Fetch file tree
    tree = _fetch_tree(owner, repo, ref)

    # Partition into directories and blobs
    dirs: list[str] = []
    blobs: list[dict] = []
    for item in tree:
        item_type = item.get("type", "")
        item_path = item.get("path", "")
        if not item_path:
            continue  # malformed tree entry; skip
        if item_type == "tree":
            dirs.append(item_path)
        elif item_type == "blob":
            blobs.append(item)

    # Add top-level directory nodes and contains edges from repo root
    top_dirs = {p for p in dirs if "/" not in p}
    for d in sorted(top_dirs):
        add_entity(d)
        add_edge(repo_id, "contains", d)

    # Add file nodes and directory → file containment edges
    for blob in sorted(blobs, key=lambda b: b.get("path", "")):
        path = blob["path"]
        lang = _detect_language(path)
        # Use the stem or basename as entity ID to keep IDs short
        stem = path.rsplit("/", 1)[-1]
        file_id = path  # use full path as unique ID
        add_entity(file_id)
        add_edge(repo_id, "contains", file_id, conf=0.9)

        # Language type edge
        if lang != "unknown":
            add_edge(file_id, "is_a", lang)

        # Directory containment
        if "/" in path:
            parent_dir = path.rsplit("/", 1)[0]
            # Find most specific existing parent
            parent_top = parent_dir.split("/")[0]
            add_edge(parent_top, "contains", file_id, conf=0.8)

    # Parse crate structure from directory names (crates/ subdirectory pattern)
    crate_dirs = [d for d in dirs if d.startswith("crates/") and d.count("/") == 1]
    for crate_path in sorted(crate_dirs):
        crate_name = crate_path.split("/")[1]
        add_entity(crate_name)
        add_edge(repo_id, "contains_crate", crate_name)
        add_edge(crate_name, "is_a", "crate")

    # Fetch and parse key files for deeper graph (bounded by max_content_files)
    content_budget = max_content_files
    priority_files = []

    # Prioritize: Cargo.toml at root and crate level, then source files
    for blob in blobs:
        p = blob["path"]
        if p in ("Cargo.toml", "package.json", "go.mod", "pyproject.toml",
                 "setup.py", "requirements.txt"):
            priority_files.insert(0, blob)
        elif p.endswith("/Cargo.toml") and p.count("/") == 2:  # crate-level
            priority_files.insert(1, blob)

    # Then add source files up to budget
    source_files = [b for b in blobs
                    if _detect_language(b["path"]) in ("rust", "python", "bash")
                    and b.get("size", 0) < 50000]  # skip very large files
    priority_files.extend(source_files)

    # Deduplicate while preserving order
    seen_paths: set[str] = set()
    ordered_files: list[dict] = []
    for blob in priority_files:
        if blob["path"] not in seen_paths:
            seen_paths.add(blob["path"])
            ordered_files.append(blob)
        if len(ordered_files) >= content_budget:
            break

    # B4: parallel HTTP prefetch — each gh api call is independent (embarrassingly parallel).
    # max_workers=5 respects GitHub rate limits (5000 req/hr authenticated, 60/hr unauthenticated).
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        file_futures = {
            blob["path"]: pool.submit(_fetch_content_raw, owner, repo, blob["path"], ref)
            for blob in ordered_files
        }

    for blob in ordered_files:
        path = blob["path"]
        lang = _detect_language(path)
        file_id = path
        try:
            content = file_futures[path].result()
        except GraphError:
            continue  # skip files we can't fetch — never abort entire graph

        if lang == "toml" and "Cargo.toml" in path:
            deps = _parse_toml_dependencies(content)
            for dep_name, dep_version in deps:
                add_entity(dep_name)
                add_edge(file_id, "depends_on", dep_name)
                # If dep is a crate in this repo, add intra-repo edge
                if dep_name.replace("_", "-") in [c.split("/")[1] for c in crate_dirs]:
                    add_edge(dep_name.replace("_", "-"), "is_a", "crate")

        elif lang == "rust":
            imports = _parse_rust_imports(content)
            for imp in imports:
                add_entity(imp)
                add_edge(file_id, "imports", imp, conf=0.9)
            # Extract pub fn / fn definitions (tractable: O(file_lines))
            for m in re.finditer(r'^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', content, re.MULTILINE):
                fn_name = m.group(1)
                fn_id = f"{file_id}::{fn_name}"
                add_entity(fn_id)
                add_edge(file_id, "defines", fn_id)
            # Extract struct / enum / trait definitions
            for kw in ("struct", "enum", "trait", "impl"):
                for m in re.finditer(rf'^(?:pub\s+)?{kw}\s+(\w+)', content, re.MULTILINE):
                    type_name = m.group(1)
                    type_id = f"{file_id}::{type_name}"
                    add_entity(type_id)
                    add_edge(file_id, "defines", type_id)

        elif lang == "python":
            imports = _parse_python_imports(content)
            for imp in imports:
                add_entity(imp)
                add_edge(file_id, "imports", imp, conf=0.9)
            # Extract function definitions
            for m in re.finditer(r'^(?:async\s+)?def\s+(\w+)', content, re.MULTILINE):
                fn_name = m.group(1)
                fn_id = f"{file_id}::{fn_name}"
                add_entity(fn_id)
                add_edge(file_id, "defines", fn_id)
            # Extract class definitions
            for m in re.finditer(r'^class\s+(\w+)', content, re.MULTILINE):
                cls_name = m.group(1)
                cls_id = f"{file_id}::{cls_name}"
                add_entity(cls_id)
                add_edge(file_id, "defines", cls_id)

        elif lang == "bash":
            # Extract function definitions
            for m in re.finditer(
                    r'^(?:function\s+)?([a-zA-Z_][a-zA-Z0-9_]*)(?:\s*\(\s*\))?\s*\{',
                    content, re.MULTILINE):
                fn_name = m.group(1)
                if fn_name not in ("if", "while", "for", "until", "case", "select"):
                    fn_id = f"{file_id}::{fn_name}"
                    add_entity(fn_id)
                    add_edge(file_id, "defines", fn_id)

    return entities, edges


# ---------------------------------------------------------------------------
# TF-IDF lexical retrieval (v1 approximation of gate-KNN attention)
#
# LARQL's gate-KNN uses learned gate vectors from transformer FFN weights.
# This implementation substitutes TF-IDF cosine similarity over tokenized
# entity names — same graph walk structure, lexical rather than neural scoring.
# Upgrade path: replace _tfidf_vector + _cosine with larql /v1/embeddings calls.
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Split identifier or text into tokens (camelCase, snake_case, path-aware)."""
    # Split on non-alphanumeric, then split camelCase
    parts = re.split(r'[^a-zA-Z0-9]+', text)
    tokens = []
    for part in parts:
        if not part:
            continue
        # Split camelCase: "WalkFfn" → ["Walk", "Ffn"]
        sub = re.sub(r'([a-z])([A-Z])', r'\1 \2', part).split()
        tokens.extend(s.lower() for s in sub)
    return tokens


def _tfidf_vector(tokens: list[str], vocab: dict[str, int], idf: dict[str, float]) -> dict[int, float]:
    """Compute TF-IDF vector as sparse dict. Tractable: O(|tokens|)."""
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    vec: dict[int, float] = {}
    for tok, count in tf.items():
        if tok in vocab and tok in idf:
            idx = vocab[tok]
            vec[idx] = (count / len(tokens)) * idf[tok]
    return vec


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    """Sparse cosine similarity. O(|a| + |b|)."""
    dot = sum(a.get(k, 0.0) * v for k, v in b.items())
    norm_a = math.sqrt(sum(v * v for v in a.values())) or 1e-9
    norm_b = math.sqrt(sum(v * v for v in b.values())) or 1e-9
    return dot / (norm_a * norm_b)


def gate_knn(query: str, entities: list[str], edges: list[tuple],
             k: int = 5) -> list[tuple[str, float]]:
    """
    TF-IDF lexical retrieval: given a query, find top-K entities by TF-IDF cosine similarity.

    Named gate_knn because it approximates the graph-walk structure of LARQL's gate-KNN
    (token → gate_vector → top-K neurons → walk), but uses TF-IDF instead of learned
    gate vectors. Pure-function: deterministic, decidable (finite vocab), tractable
    O(|entities| × avg_tokens).
    """
    if not entities:
        return []

    # Build corpus: one document per entity
    docs: list[list[str]] = [_tokenize(e) for e in entities]

    # Build vocabulary
    vocab: dict[str, int] = {}
    for doc in docs:
        for tok in doc:
            if tok not in vocab:
                vocab[tok] = len(vocab)

    # Compute IDF: log(N / df)
    N = len(docs)
    df: dict[str, int] = {}
    for doc in docs:
        for tok in set(doc):
            df[tok] = df.get(tok, 0) + 1
    idf = {tok: math.log(N / (df[tok] + 1)) for tok in vocab}

    # Query vector
    q_tokens = _tokenize(query)
    q_vec = _tfidf_vector(q_tokens, vocab, idf)
    if not q_vec:
        return []

    # Score all entities
    scores: list[tuple[str, float]] = []
    for entity, doc in zip(entities, docs):
        d_vec = _tfidf_vector(doc, vocab, idf)
        score = _cosine(q_vec, d_vec)
        if score > 0:
            scores.append((entity, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:k]


def bfs_walk(seeds: list[str], edges: list[tuple],
             hops: int = 2, max_nodes: int = 20,
             relations: Optional[list[str]] = None) -> list[str]:
    """
    BFS from seed entities following specified relations.

    DDT: deterministic (BFS order is deterministic), decidable (visited set
    bounds the walk), tractable (O(|V| + |E|) total, bounded by max_nodes).
    """
    if relations is None:
        relations = ["imports", "calls", "depends_on", "contains"]

    # Build adjacency from edges
    adj: dict[str, list[str]] = {}
    for frm, rel, to, _conf in edges:
        if rel in relations:
            adj.setdefault(frm, []).append(to)

    visited: list[str] = list(seeds)
    visited_set: set[str] = set(seeds)
    frontier = list(seeds)

    for _ in range(hops):
        if len(visited) >= max_nodes:
            break
        next_frontier: list[str] = []
        for node in frontier:
            for neighbor in adj.get(node, []):
                if neighbor not in visited_set:
                    visited_set.add(neighbor)
                    visited.append(neighbor)
                    next_frontier.append(neighbor)
                    if len(visited) >= max_nodes:
                        break
            if len(visited) >= max_nodes:
                break
        frontier = next_frontier

    return visited[:max_nodes]


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _to_lql(owner: str, repo: str, ref: str, entities: list[str],
            edges: list[tuple]) -> str:
    """Format graph as LQL INSERT triples (Vindexfile compatible)."""
    lines = [
        f"# GitHub remote vindex: {owner}/{repo}@{ref}",
        f"# Nodes: {len(entities)}, Edges: {len(edges)}",
        f"# Generated by scripts/github_graph.py",
        "",
    ]
    by_rel: dict[str, list[tuple]] = {}
    for frm, rel, to, _conf in edges:
        by_rel.setdefault(rel, []).append((frm, to))

    for rel in sorted(by_rel):
        lines.append(f"\n# {rel}")
        for frm, to in sorted(by_rel[rel]):
            lines.append(f'INSERT "{frm}", "{rel}", "{to}"')

    return "\n".join(lines) + "\n"


def _to_tsv(edges: list[tuple]) -> str:
    """Format edges as TSV."""
    lines = ["from\trelation\tto\tconfidence"]
    for frm, rel, to, conf in sorted(edges):
        lines.append(f"{frm}\t{rel}\t{to}\t{conf}")
    return "\n".join(lines) + "\n"


def _to_json_output(owner: str, repo: str, ref: str,
                    entities: list[str], edges: list[tuple]) -> str:
    """Format graph as JSON."""
    return json.dumps({
        "ok": True,
        "repo": f"{owner}/{repo}",
        "ref": ref,
        "nodes": len(entities),
        "edges": len(edges),
        "graph": {
            "entities": entities,
            "edges": [{"from": f, "rel": r, "to": t, "conf": c}
                      for f, r, t, c in edges],
        }
    }, indent=2) + "\n"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Parse args and run. Returns exit code.
    All error paths print typed JSON error to stderr and return non-zero.
    """
    import argparse
    parser = argparse.ArgumentParser(
        description="Extract a GitHub repo as a LARQL triple-store (remote vindex).")
    parser.add_argument("--repo", required=True,
                        help="GitHub repository in owner/repo format")
    parser.add_argument("--ref", default="main",
                        help="Branch, tag, or commit SHA (default: main)")
    parser.add_argument("--query", default="",
                        help="Task description for gate-KNN retrieval mode")
    parser.add_argument("--knn", type=int, default=5,
                        help="Top-K nodes for gate-KNN (default: 5)")
    parser.add_argument("--hops", type=int, default=2,
                        help="BFS hops from seed nodes (default: 2)")
    parser.add_argument("--max-nodes", type=int, default=20,
                        help="Max nodes in BFS walk (default: 20)")
    parser.add_argument("--output", choices=["lql", "json", "tsv"], default="lql",
                        help="Output format (default: lql)")
    parser.add_argument("--token-file",
                        help="File containing GitHub token")

    args = parser.parse_args()

    # Parse owner/repo
    if "/" not in args.repo:
        print(json.dumps({"ok": False, "error": "ParseError",
                          "msg": "--repo must be in owner/repo format"}),
              file=sys.stderr)
        return 2

    owner, repo = args.repo.split("/", 1)

    # Load token from file if provided
    token: Optional[str] = None
    if args.token_file:
        try:
            token = open(args.token_file).read().strip()
        except OSError as e:
            print(json.dumps({"ok": False, "error": "FileNotFound",
                              "path": args.token_file, "cause": str(e)}),
                  file=sys.stderr)
            return 2

    # Extract graph
    try:
        entities, edges = build_graph(owner, repo, args.ref, token=token)
    except GraphError as e:
        print(e.to_json(), file=sys.stderr)
        return 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": "InternalError",
                          "cause": str(e), "type": type(e).__name__}),
              file=sys.stderr)
        return 1

    # Query mode: gate-KNN + BFS walk
    try:
        if args.query:
            seeds_scored = gate_knn(args.query, entities, edges, k=args.knn)
            seeds = [e for e, _ in seeds_scored]
            expanded = bfs_walk(seeds, edges, hops=args.hops, max_nodes=args.max_nodes)

            result = {
                "ok": True,
                "repo": f"{owner}/{repo}",
                "ref": args.ref,
                "query": args.query,
                "seeds": [{"entity": e, "score": round(s, 4)} for e, s in seeds_scored],
                "expanded": expanded,
                "context_hint": (
                    f"Top-{args.knn} entities matching '{args.query}' "
                    f"+ {args.hops}-hop BFS walk. Use as retrieval context for LLM."
                ),
            }
            print(json.dumps(result, indent=2))
            return 0

        # Graph dump mode
        if args.output == "lql":
            print(_to_lql(owner, repo, args.ref, entities, edges))
        elif args.output == "tsv":
            print(_to_tsv(edges))
        else:
            print(_to_json_output(owner, repo, args.ref, entities, edges))

        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": "InternalError",
                          "cause": str(e), "type": type(e).__name__}),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
