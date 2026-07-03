# SPDX-License-Identifier: AGPL-3.0-or-later
"""START/END DAG discipline: longest-path depths and well-formedness checks.

Depth is computed over `contains` edges only (the hierarchical subgraph);
`references`/`cites` never participate in depth. λ consumes these depths.
"""
from __future__ import annotations

from collections import defaultdict

HIER = "contains"


def _adj(edges: list[dict], rel: str) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e["r"] == rel:
            adj[e["s"]].append(e["o"])
    return adj


def depths(edges: list[dict]) -> dict[str, int]:
    """Longest-path depth from START over the hierarchical subgraph (DFS + memo)."""
    adj = _adj(edges, HIER)
    depth: dict[str, int] = {"START": 0}
    # topological longest-path via iterative DFS with cycle guard
    state: dict[str, int] = {}  # 0=unvisited, 1=in-stack, 2=done

    def visit(node: str) -> None:
        stack = [(node, iter(adj.get(node, [])))]
        state[node] = 1
        while stack:
            cur, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[cur] = 2
                stack.pop()
                continue
            if state.get(nxt) == 1:
                continue  # cycle: reported by check(); skip here
            cand = depth.get(cur, 0) + 1
            if cand > depth.get(nxt, -1):
                depth[nxt] = cand
                state[nxt] = 0  # re-relax below
            if state.get(nxt, 0) == 0:
                state[nxt] = 1
                stack.append((nxt, iter(adj.get(nxt, []))))

    visit("START")
    return depth


def check(edges: list[dict]) -> list[str]:
    """Return violation strings; empty list = well-formed."""
    violations: list[str] = []
    adj = _adj(edges, HIER)
    nodes = {e["s"] for e in edges} | {e["o"] for e in edges}

    # cycle detection over hierarchical subgraph
    state: dict[str, int] = {}
    def dfs(n: str) -> bool:
        state[n] = 1
        for m in adj.get(n, []):
            if state.get(m) == 1:
                return True
            if state.get(m, 0) == 0 and dfs(m):
                return True
        state[n] = 2
        return False
    for n in sorted(nodes):
        if state.get(n, 0) == 0 and dfs(n):
            violations.append(f"cycle: hierarchical subgraph has a cycle through {n}")
            break

    # START-reachability over ALL edges
    all_adj: dict[str, list[str]] = defaultdict(list)
    rev_adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        all_adj[e["s"]].append(e["o"])
        rev_adj[e["o"]].append(e["s"])
    seen = {"START"}
    frontier = ["START"]
    while frontier:
        n = frontier.pop()
        for m in all_adj.get(n, []):
            if m not in seen:
                seen.add(m)
                frontier.append(m)
    for n in sorted(nodes - seen - {"START"}):
        violations.append(f"not-START-reachable: {n}")

    # END-coreachability over ALL edges
    coseen = {"END"}
    frontier = ["END"]
    while frontier:
        n = frontier.pop()
        for m in rev_adj.get(n, []):
            if m not in coseen:
                coseen.add(m)
                frontier.append(m)
    for n in sorted(nodes - coseen - {"END"}):
        # objects that are pure attributes (titles, cite keys, categories) are exempt:
        # only file/section/dir nodes must reach END
        if n.startswith(("cite:", "category:", "matter:")) or " " in n:
            continue
        if n == "START" and "END" in coseen and seen & coseen:
            continue
        violations.append(f"not-END-coreachable: {n}")
    return violations
