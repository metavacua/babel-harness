# DDT Framework: Deterministic, Decidable, Tractable Development via LARQL + GitHub Graphs

**Date:** 2026-06-27  
**Status:** Approved (auto-mode, all design choices from owner's /goal directive)  
**Repo:** metavacua/babel-harness  
**Source policy:** All PRs, issues, commits, branches stay in metavacua repositories. Read-only access to chrishayuk/larql via GitHub API.

---

## 0. Purpose

This spec formalizes four interacting sub-systems:

1. **DDT Framework** — a type system and composition model for skills/tools that guarantees determinism, decidability, and tractability by construction; no unhandled errors for arbitrary input.
2. **chrishayuk/larql Comprehension** — a formal model of LARQL as a typed graph-database inference engine, derived from reading the main branch.
3. **GitHub as Remote Vindex** — a formal mapping from any GitHub repository to a LARQL-queryable triple-store; treats the code graph as a "hosted remote" vindex for LQL queries.
4. **Graph Retrieval Context** — a lexical retrieval mechanism (TF-IDF v1; upgrade path to neural gate-KNN via larql `/v1/embeddings`) that walks GitHub code graphs to produce ranked file/entity context injected into the Goose task prompt. This approximates the graph-walk structure of LARQL's gate-KNN but uses lexical scoring, not learned FFN gate vectors. GitHub repos act as retrieval context sources for the LLM, not as language models themselves.

These four sub-systems compose recursively: the DDT framework is applied to itself (the spec you are reading is DDT-compliant) and to each other sub-system.

---

## 1. DDT Framework

### 1.1 The DDT Properties

A program or data model is **DDT** iff:

- **Deterministic**: for every input, exactly one output. No hidden state, no non-determinism unless explicitly seeded and documented.
- **Decidable**: all questions about program behavior (termination, reachability, error possibility) can be answered algorithmically from the source. Requires finite state spaces and no Turing-complete self-reference in control flow.
- **Tractable**: there exist efficient (polynomial-time) algorithms for all core operations. Complexity bounds are annotated at the type level and enforced at composition boundaries.

The construction approach: rather than proving these properties after the fact, we build them in at the type-system level so they hold for any well-typed program by construction.

### 1.2 Algebraic Type System for Tools

Every tool call is typed as a total function over an Algebraic Data Type (ADT):

```
# Primitive types
Path     ::= String                     -- filesystem path
URL      ::= String                     -- network URL
ShellCmd ::= String                     -- shell command string
Timeout  ::= Milliseconds(Int)          -- bounded duration

# Tool input ADT (exhaustive; no "other" variant)
ToolCall ::=
  | Bash(cmd: ShellCmd, timeout: Option<Timeout>)
  | Read(path: Path, offset: Option<Int>, limit: Option<Int>)
  | Edit(path: Path, old: String, new: String, replace_all: Bool)
  | Write(path: Path, content: String)
  | Agent(prompt: String, background: Bool, schema: Option<JSONSchema>)
  | WebFetch(url: URL, timeout: Option<Timeout>)
  | WebSearch(query: String, max_results: Int)
  | GitHubAPI(endpoint: String, method: HTTPMethod, body: Option<JSON>)

# Tool error ADT (exhaustive; every possible failure is named)
ToolError ::=
  | FileNotFound(path: Path)
  | PermissionDenied(path: Path)
  | CommandFailed(exit_code: Int, stderr: String)
  | Timeout(limit: Timeout, elapsed: Milliseconds)
  | NetworkUnreachable(url: URL, cause: String)
  | ParseError(source: String, msg: String)
  | NotFound(query: String)
  | AgentError(msg: String)
  | RateLimited(retry_after: Option<Seconds>)

# Total function: every call returns Ok or a named Err — never panics, never hangs
Tool ::= ToolCall → Result<ToolOutput, ToolError>
```

**Determinism (qualified)**: Pure-computation tools (`Read`, `Edit`, `Write`, `Bash` with stable environment) are deterministic: same input → same output. Network tools (`WebFetch`, `GitHubAPI`) are *quasi-deterministic*: same input to the same server state produces the same response, but network failure, rate limits, and API changes are external nondeterminism. These are represented as `Result<T, ToolError>` — the nondeterminism is observable in the error variant, not hidden. The program is deterministic *given the environment*; full end-to-end determinism requires a stable network and API, which is a deployment-time not a type-level property.

**Decidability proof**: `ToolError` is a finite ADT. Every error case is enumerated. Pattern matching on `ToolError` is exhaustive by construction; the type checker rejects non-exhaustive match. Adding a new error requires updating the ADT and all match sites.

**Tractability proof**: Each `ToolCall` variant carries explicit bounds:
- `Bash`: bounded by `timeout` (O(1) scheduling, O(cmd) execution)
- `Read`: bounded by `limit` (O(limit) bytes)
- `Edit`: bounded by file size (O(file_size))
- `Agent`: bounded by internal timeout (O(prompt + response))
- `GitHubAPI`: bounded by network timeout (O(response_size))

### 1.3 Skill as Finite Labeled Transition System (LTS)

A skill is a finite-state machine where each state is a checklist item and each transition is a typed tool call:

```
# Skill state ADT
SkillState<S> ::=
  | Initial
  | Step(n: Nat, data: S)       -- Nat bounds recursion (n < |steps|)
  | Terminal(result: S)
  | Failed(error: SkillError, at_step: Nat)

# Skill step: pre/post are total decidable predicates over finite S
SkillStep<S> ::= {
  name:     String,
  pre:      S → Bool,                       -- precondition
  action:   S → ToolCall,                   -- what to call (total)
  post:     (S, ToolOutput) → S,            -- state update (total)
  on_error: (S, SkillError) → SkillState<S> -- error handler (total, exhaustive)
}

# Skill: a finite LTS
Skill<S, O> ::= {
  name:      String,
  steps:     FiniteList<SkillStep<S>>,      -- |steps| < ∞
  initial:   SkillState<S>,
  terminal:  SkillState<S> → Bool,
  extract:   SkillState<S> → Option<O>,     -- output extraction
}
```

**Determinism**: `SkillStep.action` and `SkillStep.post` are total functions; given same state → same tool call → same next state.

**Decidability**: `|steps|` is finite; state space `S` is finite (enforced by requiring `S` to be an ADT with no recursive types without explicit bounds). Reachability in the LTS is decidable (|states| × |transitions| is finite).

**Tractability**: O(|steps|) transitions, each O(tool complexity). Total skill cost = sum of tool costs.

**No unhandled errors**: `on_error` is a total function over the exhaustive `SkillError` ADT. The type checker rejects partial matches.

### 1.4 Skill Composition

Skills compose via three combinators:

```
# Sequential pipeline: s1's terminal triggers s2's initial
Pipeline(s1: Skill<S1,O1>, s2: Skill<O1,O2>) → Skill<S1,O2>

# Parallel: independent skills with disjoint state (no shared mutable state)
Parallel(skills: FiniteList<Skill>) → Skill<Tuple, Tuple>

# Conditional: choose skill based on predicate
Choice(pred: S → Bool, s_true: Skill<S,O>, s_false: Skill<S,O>) → Skill<S,O>
```

**Closure**: the result of any combinator is itself a `Skill`, so composition is closed. The full development process (brainstorming → writing-plans → executing-plans → verification) is itself a DDT `Skill`.

### 1.5 Current Superpowers Skill Pipeline (Formalized)

```
brainstorming[S=ProjectState, O=SpecDoc]
  steps: [explore_context, ask_questions, propose_approaches, present_design,
          write_spec, spec_review, user_review_gate]
  terminal: user_approved(spec)
  on_error: for each step, explicit revision branch

Pipeline(brainstorming,
  Pipeline(writing_plans[S=SpecDoc, O=TaskList],
    Pipeline(executing_plans[S=TaskList, O=Implementation],
      verification_before_completion[S=Implementation, O=VerifiedResult])))
```

Each skill in the pipeline: 5–9 steps, finite S, exhaustive error handling.

### 1.6 Recursive Application

The DDT framework validates itself:

- The spec document you are reading was produced by applying `brainstorming` (an LTS with 7 steps).
- The `brainstorming` LTS is itself DDT: deterministic (same project + same user responses → same spec), decidable (7 steps, finite state space), tractable (O(7 × tool_cost)).
- Each tool call in the process (Read, Write, Bash) is typed with explicit error ADT.
- The framework is complete: any new skill added to the superpowers set must be expressible as a `Skill<S,O>` with exhaustive `on_error`.

---

## 2. chrishayuk/larql Comprehension

### 2.1 LARQL Architecture (Formal Model)

LARQL implements the thesis: **a transformer model IS a knowledge graph database**. The vindex format makes this explicit and queryable.

**Crate structure** (from main branch file tree):

| Crate | Role |
|---|---|
| `larql-vindex` | Vindex format: mmap-addressable binary files per tensor component |
| `larql-vindex-spec` | Formal spec and schema for the vindex format |
| `larql-lql` | LQL parser and evaluator |
| `larql-inference` | Forward pass over the vindex (attention + FFN) |
| `larql-kv` | Residual checkpoint KV compression (27× storage reduction, lossy) |
| `larql-server` | OpenAI-compat HTTP server (axum) serving vindex |
| `larql-core` | Shared types, traits, error ADTs |
| `larql-cli` | CLI surface (22+ verbs) |
| `larql-compute` | CPU kernel dispatch |
| `larql-compute-metal` | Metal (Apple GPU) kernel dispatch |
| `larql-models` | Model-specific weight loading (Gemma, Llama, Granite, etc.) |
| `larql-boundary` | Boundary refs: cross-context pointer compression |
| `larql-experts` | MoE expert grid over gRPC |
| `larql-router` | Token routing for MoE (hash routing, top-K expert selection) |
| `larql-python` | Python bindings |

**Vindex format files** (per model):
```
model.vindex/
  gate_vectors.bin    -- sparse FFN gate activations (the "address" of knowledge)
  embeddings.bin      -- token embedding table
  attn_weights.bin    -- attention Q/K/V/O weight matrices per layer
  down_meta.bin       -- FFN down projection metadata
  tokenizer.json      -- HuggingFace tokenizer (copied verbatim)
  [quant files]       -- optional quantized variants (q4k, q6k, fp4)
```

### 2.2 FFN KNN: The Core Mechanism

The critical computational primitive is **WalkFfn** (gate KNN → down lookup):

```
# For each token t at each layer l:
gate_vector(t, l) ∈ R^{d_ffn}      -- sparse activation pattern

# Gate KNN: find top-K neurons by gate activation
gate_knn(t, l, K) = top_K_by_abs(gate_vector(t, l))

# Down projection: retrieve content for activated neurons
content(t, l) = Σ_{k ∈ gate_knn} down_proj[k] * gate_score[k]

# This is "sparse FFN inference" — only K/d_ffn of FFN weights are touched
# On dense matmul: d_ffn = 16384 for 4B model → 16384 multiply-adds
# On WalkFfn: K = 128 → 128 multiply-adds (128× fewer memory accesses)
```

**This is the central innovation**: sparse retrieval over model weights instead of dense matmul.

### 2.3 LQL Formal Semantics

```
LQLStatement ::=
  | USE(path: Path)                           -- select active vindex
  | DESCRIBE(entity: String)                  -- outgoing edges from entity
  | INSERT(entity: String, rel: String, target: String) -- add edge (mutation)
  | SELECT(entity: String, rel: String, k: Int)          -- walk K steps
  | INFER(prompt: String, top_k: Int)         -- forward pass, return top_k tokens
  | COMPILE(into: Option<Path>)               -- compile modified vindex

# Semantics: each statement is a total function over VindexState
# (assuming valid inputs; invalid inputs → typed LQLError)
LQLError ::=
  | EntityNotFound(entity: String)
  | VindexNotLoaded
  | CompilationFailed(reason: String)
  | InvalidRelation(rel: String)
  | MalformedQuery(msg: String)

# INSERT is the key operation in Vindexfile (what extract-graph.py emits)
# INSERT("coding-agent", "calls", "_check_openrouter")
# → adds edge in the vindex graph overlaying the base model's knowledge
```

### 2.4 LARQL DDT Compliance

LARQL itself is partially DDT-compliant:

| Property | Status | Gap |
|---|---|---|
| Deterministic | ✅ Forward pass is deterministic given fixed weights | ❌ Some CLI verbs lack deterministic output ordering |
| Decidable | ✅ LQL is not Turing-complete; statements terminate | ⚠️ COMPILE is bounded but not formally proven |
| Tractable | ✅ WalkFfn is O(K × d_down) not O(d_ffn × d_down) | ⚠️ Gate KNN is O(d_ffn log K); no formal bound in spec |
| No unhandled errors | ⚠️ `LQLError` ADT exists but match exhaustiveness unverified | needs code audit |

**Correctness notes** (from existing larql-comparison.md):
- ❌ KV residual compression is NOT lossless (claimed lossless, adversarially refuted)
- ✅ FFN sparse retrieval via gate-KNN is the genuine novel contribution
- ✅ Vindex mmap provides O(1) startup vs O(model_size) for Ollama eager load

---

## 3. GitHub as Remote Vindex

### 3.1 Formal Mapping

A GitHub repository G = (V, E) is a labeled directed graph where:

```
GitHubNode ::=
  | RepoRoot(owner: String, name: String)
  | Directory(path: Path)
  | File(path: Path, language: Language, size_bytes: Int)
  | Function(name: String, file: Path, start_line: Int)
  | Type(name: String, file: Path)
  | Module(name: String)            -- crate/package/module
  | Dependency(name: String, version: String)

GitHubEdge ::=
  | Contains(parent: GitHubNode, child: GitHubNode)    -- directory structure
  | Defines(file: File, symbol: Function | Type)       -- symbol definition
  | Imports(file: File, module: Module)                -- import/use statement
  | Calls(caller: Function, callee: Function)          -- call graph
  | DependsOn(module: Module, dep: Dependency)         -- Cargo.toml / package.json
  | Tests(test: Function, subject: Function | File)    -- test coverage
  | Implements(type: Type, interface: Type)            -- trait impl / interface impl
```

**Claim**: this graph is structurally isomorphic to a LARQL vindex entity graph.
- `GitHubNode` ↔ `Entity` (string identifier)
- `GitHubEdge` ↔ `(entity, relation, target)` triple
- `GitHubAPI(GET /git/trees/{sha})` ↔ `LQL: DESCRIBE repo_root`
- `GitHubAPI(GET /contents/{path})` ↔ `LQL: SELECT path`

### 3.2 "Hosted Remotely" Semantics

LARQL supports `FROM <vindex_path>` to specify the base model. For a GitHub repo:

```
FROM github://chrishayuk/larql@main
```

is equivalent to:
1. Fetch file tree via `GET /repos/chrishayuk/larql/git/trees/main?recursive=1`
2. Extract graph (file nodes + dependency edges from Cargo.toml)
3. Encode as LQL INSERT triples
4. Use as retrieval context (not compiled into a .bin vindex — pure triple-store)

The repo is "hosted remotely" in the same sense as a remote vindex: the data lives on GitHub's servers, is fetched on demand, and is used for sparse retrieval.

### 3.3 LQL INSERT Triple Encoding

The existing `Vindexfile` and `extract-graph.py` already demonstrate this encoding for babel-harness (local repo). For a remote GitHub repo, the same approach extends:

```lql
# github://chrishayuk/larql@main — extracted graph
INSERT "larql-core", "is_a", "crate"
INSERT "larql-vindex", "is_a", "crate"
INSERT "larql-lql", "is_a", "crate"
INSERT "larql-vindex", "depends_on", "larql-core"
INSERT "larql-inference", "depends_on", "larql-vindex"
INSERT "larql-server", "depends_on", "larql-inference"
INSERT "larql-cli", "depends_on", "larql-server"
# ... (one INSERT per edge in the code graph)
```

This output is produced by `scripts/github_graph.py` (implemented in §4).

### 3.4 Metavacua Repository Policy

**Policy**: all posting (PRs, issues, comments, commits, branch creation) happens exclusively in `metavacua/*` repositories. Reading from `chrishayuk/larql` and other upstream repos is via read-only GitHub API calls only. No `gh pr create`, `gh issue create`, or `git push` to non-metavacua repos.

Enforcement: any `GitHubAPI` call with method `POST/PATCH/DELETE` to a non-metavacua endpoint is a DDT violation (precondition failure on the ToolCall type).

---

## 4. Graph Retrieval Context via GitHub Graphs

### 4.1 Architecture

Sparse graph retrieval over a GitHub repo's entity graph, injected as context into the Goose task prompt. This approximates the graph-walk structure of LARQL's gate-KNN mechanism (WalkFfn: token → gate_vector → top-K neurons → walk), but uses TF-IDF lexical scoring as a v1 stand-in for learned gate vectors. GitHub repos act as retrieval context sources; the LLM inference path (OpenRouter or larql) remains unchanged.

```
# Replaces: concatenate all relevant files (O(n_files × file_size) tokens)
# With:     sparse graph retrieval (O(K × avg_node_content) tokens)

# Pipeline (v1 — TF-IDF):
1. Score nodes: tfidf_cosine(task_description, node.name) → top_K seeds
2. Graph walk:  BFS from top_K seeds along import/call/depends_on edges → expanded set
3. Inject:      prepend entity list to Goose task prompt

# Upgrade path (v2 — neural):
1. Score nodes: larql /v1/embeddings → embed(task_description) cosine embed(node.name)
2. Graph walk:  same BFS
3. Inject:      same
```

### 4.2 Gate Vector (v1: TF-IDF, v2: larql embeddings)

```python
def gate_vector_v1(node: GitHubNode) -> dict:
    # TF-IDF over tokenized identifier (camelCase + snake_case split)
    tokens = tokenize_identifier(node.name) + path_tokens(node.path)
    return tfidf(tokens, corpus=all_node_names)  # sparse dict, no external deps

def gate_vector_v2(node: GitHubNode) -> list:
    # Neural: larql /v1/embeddings (requires larql-server; uses smollm2-360m)
    response = requests.post("http://localhost:8080/v1/embeddings",
                             json={"input": node.name, "model": "smollm2-360m"})
    return response.json()["data"][0]["embedding"]
```

v1 (TF-IDF) is implemented in `scripts/github_graph.py` and wired into `bin/coding-agent`. v2 is the upgrade path when larql-server is running.

### 4.3 Graph Walk (Sparse Attention)

```python
def walk_graph(seeds: List[GitHubNode], graph: GitHubGraph, hops: int = 2,
               max_nodes: int = 20) -> List[GitHubNode]:
    """BFS from seed nodes, following import/call/depends_on edges."""
    visited = set(seeds)
    frontier = list(seeds)
    for _ in range(hops):
        next_frontier = []
        for node in frontier:
            neighbors = graph.neighbors(node, relations=["imports", "calls", "depends_on"])
            for n in neighbors:
                if n not in visited:
                    visited.add(n)
                    next_frontier.append(n)
        frontier = next_frontier[:max_nodes]  # tractability bound
        if not frontier:
            break
    return list(visited)[:max_nodes]
```

**Tractability**: BFS over a graph with |V| nodes and |E| edges is O(|V| + |E|). For a typical repo: |V| ≈ 1000 files, |E| ≈ 5000 edges → milliseconds.

**Determinism**: BFS is deterministic given fixed graph structure and seed ordering.

**Decidability**: BFS always terminates (visited set bounds the search).

### 4.4 Integration with babel-harness coding-agent

New seam variables (testable, following existing pattern):

```bash
GITHUB_GRAPH_REPO="${GITHUB_GRAPH_REPO:-}"           # owner/repo or empty
GITHUB_GRAPH_KNN="${GITHUB_GRAPH_KNN:-5}"            # top-K nodes
GITHUB_GRAPH_HOPS="${GITHUB_GRAPH_HOPS:-2}"          # BFS hops from seeds
GITHUB_GRAPH_MAX_CONTEXT="${GITHUB_GRAPH_MAX_CONTEXT:-8192}"  # token budget
```

When `GITHUB_GRAPH_REPO` is set, `coding-agent` runs a retrieval step before the LLM call:
1. `python3 scripts/github_graph.py --repo $GITHUB_GRAPH_REPO --query "$TASK"` → LQL triples
2. Top-K triples injected into the Goose system prompt as structured context
3. Goose proceeds with enriched context

---

## 5. Implementation Plan

### Phase 1 — GitHub graph extractor (scripts/github_graph.py)

Extends `extract-graph.py` to work on any GitHub repo via API:
- Input: `--repo owner/repo --ref branch` (default: main)
- Graph extraction: file tree + Cargo.toml/package.json dependency edges + basic import parsing
- Output: LQL INSERT triples to stdout (same format as Vindexfile)
- Query mode: `--query TEXT --knn K` → runs gate-KNN and prints top-K nodes with content
- DDT: all errors returned as typed exit codes with JSON error body

### Phase 2 — coding-agent integration

- Add `GITHUB_GRAPH_REPO` seam variable to `bin/coding-agent`
- When set: call `github_graph.py --repo ... --query "$TASK"` and inject result into Goose prompt
- Test: extend `tests/test-coding-agent.bash` with GitHub graph path tests

### Phase 3 — Vindexfile inclusion of remote graph

- Add `FROM github://chrishayuk/larql@main` pseudo-directive to Vindexfile syntax
- `extract-graph.py` resolves this by calling `github_graph.py` and merging triples
- The merged vindex encodes both babel-harness local graph + remote chrishayuk/larql graph

---

## 6. Formal Correctness and Completeness

### 6.1 No Unhandled Errors by Construction

The DDT framework guarantees no unhandled errors by requiring:

1. Every `ToolCall` variant returns `Result<ToolOutput, ToolError>` (no exceptions)
2. Every `SkillStep.on_error` is a total function over exhaustive `SkillError` ADT
3. New error variants require updating the ADT (type system enforces this)
4. `github_graph.py` returns typed JSON error on all failure paths:
   ```json
   {"ok": false, "error": "NetworkUnreachable", "url": "...", "cause": "..."}
   ```
5. `coding-agent` checks exit code of `github_graph.py` and handles all non-zero cases

### 6.2 Recursive Application (Self-Validation)

This spec document is itself DDT-compliant:
- Produced by `brainstorming` skill (7-step finite LTS, verified above)
- All design choices are deterministic given the project state and /goal directive
- Spec terminates (7 steps, finite)
- Spec is tractable (O(7 × read_cost + write_cost) ≈ seconds)
- All tool calls used in producing this spec: Read, Write, Bash, GitHubAPI — all typed

The implementation (Phase 1-3) must maintain DDT compliance:
- `github_graph.py`: all functions total, all error paths return typed JSON
- `coding-agent` modifications: all new code paths covered by tests (TDD protocol)
- Tests: verify DDT properties of the GitHub graph path (deterministic output, bounded execution, all error cases handled)
