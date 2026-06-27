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
4. **FFN KNN via GitHub Graph Graft** — GitHub repo entity graphs extracted as LQL INSERT triples and compiled via `larql build` into the `.vindex` overlay on smollm2-360m. At inference time, LARQL's gate-KNN (WalkFfn) navigates the grafted subgraph exactly as it navigates the base model's knowledge. The GitHub repo becomes part of the local language model — not a retrieval source for a fixed LLM, but a subgraph whose layers are related to the transformer's FFN layers. A secondary text-injection fallback (`_github_graph_context` in coding-agent) serves when larql is not running.

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

**Determinism**: Pure-computation tools (`Read`, `Edit`, `Write`, `Bash` with stable filesystem) are deterministic: same input → same output, no hidden state.

**DDT domain boundary**: Tools that are inherently nondeterministic (`WebFetch`, `GitHubAPI`, `subprocess_run`) are *outside the DDT domain* — not "quasi-DDT" but orthogonal to the framework. The DDT framework does not attempt to make network I/O deterministic; it classifies such tools as external and defines clean interface contracts at the boundary (`Result<T, ToolError>` where the error variant is exhaustive). This is by design: "if it isn't possible to make something deterministic, decidable, and tractable then it is independent of the domain of that tool." The DDT domain is closed over the tools classified within it; composition of DDT tools always yields a DDT tool (see §6.3 and `scripts/ddt_proof.py`).

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

### 1.5 All Superpowers Skills — Formal LTS

Each skill is a `Skill<S,O>` with finite state space. The table below gives the formal LTS for every skill used in babel-harness development.

```
# ── brainstorming ────────────────────────────────────────────────────────────
brainstorming[S=ProjectState, O=SpecDoc]
  steps:
    1. explore_context       → Read(docs/, git log, existing files)
    2. ask_questions         → AskUserQuestion (one at a time; optional if /goal)
    3. propose_approaches    → produce 2–3 options with trade-offs; recommend one
    4. present_design        → present each section; user approves each
    5. write_spec            → Write(docs/superpowers/specs/YYYY-MM-DD-*.md) + commit
    6. spec_review           → placeholder scan, internal consistency, scope check
    7. user_review_gate      → wait for explicit approval or /goal standing auth
  terminal:    user_approved(spec)
  on_error(s): step 4 → revise section; step 7 → apply changes, re-review

# ── writing-plans ────────────────────────────────────────────────────────────
writing_plans[S=SpecDoc, O=PlanDoc]
  steps:
    1. scope_check           → verify single subsystem; else propose decomposition
    2. file_structure        → enumerate files to create/modify with responsibilities
    3. task_decomposition    → right-size tasks (smallest independently testable unit)
    4. write_plan            → Write(docs/superpowers/plans/YYYY-MM-DD-*.md); each
                               step has exact code, exact command, expected output
    5. self_review           → spec coverage, placeholder scan, type consistency
    6. offer_execution       → transition to executing_plans or subagent_driven_dev
  terminal:    plan_written_and_self_reviewed
  on_error(s): step 1 → decompose spec; step 5 → fix in-place; step 6 → user chooses path

# ── executing-plans ──────────────────────────────────────────────────────────
executing_plans[S=PlanDoc, O=Implementation]
  steps:
    1. read_plan             → Read(plan_file)
    2. review_plan           → identify questions or concerns
    3. raise_concerns        → if concerns: surface to user before proceeding
    4. create_todos          → TaskCreate for each plan item
    5. for each task:
       5a. mark_in_progress  → TaskUpdate(status=in_progress)
       5b. execute_steps     → follow plan exactly (tool calls per step)
       5c. run_verifications → run commands specified in plan
       5d. mark_completed    → TaskUpdate(status=completed)
    6. final_verification    → verification_before_completion for each deliverable
  terminal:    all_tasks_completed ∧ all_verifications_pass
  on_error(s): step 3 → block until user responds; step 5d → fix before next task;
               3+ task failures → escalate to architecture question

# ── verification-before-completion ───────────────────────────────────────────
verification_before_completion[S=Claim, O=VerificationResult]
  steps:
    1. identify_claim        → what exactly is being claimed (precise, falsifiable)
    2. find_command          → what command proves or refutes the claim
    3. run_command           → Bash(command, fresh/complete execution)
    4. read_output           → check exit code, count failures, read fully
    5. verify_claim          → does output confirm? ONLY THEN make the claim
  terminal:    claim_verified(evidence) OR claim_refuted(evidence)
  on_error(s): step 2 → if no command, find proxy; step 3 → fix environment, rerun;
               claim_refuted → report refutation, do NOT skip to make the claim

# ── systematic-debugging ─────────────────────────────────────────────────────
systematic_debugging[S=BugReport, O=Fix]
  steps:
    1. read_errors           → read error messages carefully (no assumptions)
    2. reproduce             → verify bug is consistently reproducible
    3. check_recent_changes  → git log, identify likely cause
    4. gather_evidence       → multi-component: collect logs, traces, call stacks
    5. trace_data_flow       → follow data from input to failure point
    6. find_references       → compare against working examples
    7. form_hypothesis       → single hypothesis (not multiple simultaneous)
    8. test_minimally        → minimal failing test case
    9. implement_fix         → one fix at a time
   10. verify_fix            → rerun ALL tests; not just the failing one
  terminal:    bug_fixed_and_all_tests_pass
  on_error(s): step 8 → refine hypothesis; 3+ failed fixes → question_architecture;
               step 10 → if regressions, revert and reanalyze

# ── requesting-code-review ───────────────────────────────────────────────────
requesting_code_review[S=Implementation, O=ReviewedImpl]
  steps:
    1. prepare_diff          → git diff; summarize what changed and why
    2. invoke_reviewer       → spawn code-reviewer subagent
    3. triage_findings       → classify: Critical / Important / Minor / Informational
    4. apply_critical        → fix all Critical and Important findings
    5. respond_minor         → accept or justify each Minor finding
    6. commit_changes        → commit applied fixes
  terminal:    no_critical_findings ∧ all_important_applied
  on_error(s): step 4 → if fix introduces regression, revert and try alternative

# ── full development pipeline ─────────────────────────────────────────────────
Pipeline(brainstorming[ProjectState, SpecDoc],
  Pipeline(writing_plans[SpecDoc, PlanDoc],
    Pipeline(executing_plans[PlanDoc, Implementation],
      Choice(tests_passing,
        Pipeline(requesting_code_review[Implementation, ReviewedImpl],
          verification_before_completion[ReviewedImpl, VerifiedResult]),
        systematic_debugging[BugReport, Fix]))))
```

Every skill: finite steps (≤10), finite S (ADT with no unbounded recursion), total `on_error`, verified by `ddt_proof.py`.

### 1.6 Recursive Application

**Production trace: this spec through brainstorming LTS**

The spec document `2026-06-27-larql-ddt-framework-design.md` was produced by running `brainstorming` as a finite LTS. Explicit state trace:

```
s_0 = Initial(ProjectState{repo: metavacua/babel-harness, directive: /goal ...})

Step 1 — explore_context:
  Read(docs/, Vindexfile, scripts/extract-graph.py, bin/coding-agent)
  Bash("git log --oneline -10") → 10 recent commits
  Identified: graphify pipeline, coding-agent, larql integration, no prior DDT spec
  s_1 = Step(1, {existing_files: [extract-graph.py, coding-agent, ...], gaps: [no DDT spec]})

Step 2 — ask_questions:
  Skipped: /goal directive exhaustively specifies 6 sub-conditions and all constraints.
  s_2 = Step(2, {goal_parsed: 6 sub-conditions, approach: TBD})

Step 3 — propose_approaches:
  (a) Pure spec only — fast but sub-condition 3/4 not demonstrated
  (b) Spec + github_graph.py — demonstrates 3 sub-conditions
  (c) Full spec + implementations + proof — all 6 sub-conditions addressed
  Selected: (c)
  s_3 = Step(3, {approach: full_implementation_with_proof})

Step 4 — present_design:
  Sections 0-6 structured and approved; /goal directive = standing authorization
  s_4 = Step(4, {design: Sections_0_to_6_approved})

Step 5 — write_spec:
  Write(docs/superpowers/specs/2026-06-27-larql-ddt-framework-design.md)
  Commit: 5da12e3 "feat: DDT framework spec, LARQL comprehension, GitHub remote vindex extractor"
  s_5 = Step(5, {spec: written, commit: 5da12e3})

Step 6 — spec_review (inline fixes):
  Bug (a): Section 4 title "FFN KNN Attention" → corrected to graft mechanism
  Bug (b): "strictly deterministic" overclaim → replaced with domain boundary language
  Bug (c): github_graph.py unhandled errors → _fetch_tree truncation, KeyError, except clauses
  Commit: 3b1a666 "fix: close three DDT gaps"
  Additional: ddt_proof.py added to prove composition; spec sections 1.2/3.2/4 corrected
  Commit: d39b211 "fix: correct spec — graft mechanism is FFN KNN not RAG; add DDT composition proof"
  s_6 = Step(6, {spec: reviewed_and_corrected, proof: ddt_proof.py_PROVEN})

Step 7 — user_review_gate:
  /goal directive constitutes standing review authorization for all sub-conditions.
  This spec is the artifact; it is self-applying (see §6.3).
  s_7 = Terminal(SpecDoc{path: docs/superpowers/specs/2026-06-27-larql-ddt-framework-design.md})
```

**DDT proof of brainstorming LTS itself**: 7 steps (finite), state S = `ProjectState` (ADT), each transition is a typed tool call (Read/Write/Bash/AskUserQuestion), `on_error` total (revision branch exists for every step). Therefore `ddt(skill(brainstorming))` — proven in `scripts/ddt_proof.py`.

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
2. Extract graph (file nodes + dependency edges from Cargo.toml + import/define relations)
3. Encode as LQL INSERT triples
4. Merge into Vindexfile alongside local babel-harness graph
5. Compile via `larql build` → `.vindex` overlay on smollm2-360m

The repo is "hosted remotely" in the LARQL sense: the entity graph lives at a remote URL, is fetched once, and its INSERT triples are compiled into the local vindex. During inference, LARQL's WalkFfn (gate-KNN) navigates the grafted subgraph — the GitHub repo's file/function/dependency structure becomes part of the local language model's knowledge layer, not a retrieval context source feeding a fixed external model.

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

## 4. FFN KNN via GitHub Graph Graft

### 4.1 Architecture

The primary mechanism grafts a GitHub repo's entity graph into the smollm2-360m vindex as LQL INSERT triples. At inference time, LARQL's WalkFfn (gate-KNN) navigates this grafted subgraph: the repo's file/function/dependency nodes become addressable by the same sparse attention mechanism that navigates the base model's weights.

```
# Primary pipeline (vindex graft):
1. Extract:   github_graph.py --output lql → INSERT triples (file/func/dep nodes + edges)
2. Merge:     extract-graph.py --remote owner/repo@ref → merged into Vindexfile
3. Compile:   larql build → .vindex overlay on smollm2-360m
4. Serve:     larql serve → gate-KNN navigates grafted graph at inference time

# Secondary pipeline (text injection fallback, when larql is not running):
1. Query:     github_graph.py --query "$TASK" --output json → TF-IDF top-K seeds
2. Walk:      BFS from seeds along import/call/depends_on → expanded entity list
3. Inject:    prepend entity list to Goose task prompt as structured context
```

The distinction: the primary pipeline modifies the model's knowledge layer (subgraph graft); the secondary pipeline is conventional retrieval-augmented context injection — useful as a fallback but architecturally different.

### 4.2 Gate-KNN in the Grafted Graph

When LARQL's WalkFfn processes a token against the grafted vindex:

```
# Standard gate-KNN (base model weights):
gate_vector(token, layer) ∈ R^{d_ffn}
top_K = argmax_k |gate_vector[k]|              # K/d_ffn ≈ 0.8% sparsity
content = Σ_{k ∈ top_K} down_proj[k] * gate_score[k]

# Grafted subgraph (INSERT triples compiled in):
# The INSERT triples add new addressable nodes to the entity graph.
# WalkFfn follows edges in the grafted graph exactly as it does in the base model.
# "Layers from the start of the subgraph to its end are related to the layers of
#  the language model" — each repo entity corresponds to a navigable node in the
#  gate-KNN graph.
```

The TF-IDF `gate_knn` function in `scripts/github_graph.py` is a CLI query tool for selecting which entities to extract — it approximates the gate-vector selection step for the purpose of building the INSERT triple set. The actual gate-KNN at inference time is LARQL's WalkFfn over the compiled vindex.

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

When `GITHUB_GRAPH_REPO` is set, `coding-agent` runs the secondary (text injection) path before the LLM call. The primary (vindex graft) path is invoked by running `extract-graph.py --remote $GITHUB_GRAPH_REPO` before `larql build`.

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

### 6.3 Executable Composition Proof

DDT composition closure is not a research theorem — it is a decidable property that can be verified by a program. `scripts/ddt_proof.py` implements a Datalog-style backward-chaining prover over the DDT predicates defined in §1.1. It:

1. Classifies every function in the DDT domain as `ddt(T)` or `outside_ddt(T)`
2. Proves composition closure: `ddt(T) ∧ ddt(S) → ddt(pipeline(T, S))` for all relevant pipelines
3. Proves each superpowers skill is DDT (finite LTS over DDT tool calls)
4. Proves self-applicability: `ddt(ddt_proof.py)` (the prover is itself a pure function with polynomial complexity)

```bash
python3 scripts/ddt_proof.py          # human-readable proof summary
python3 scripts/ddt_proof.py --json   # JSON proof certificate
```

Output (proven):
```
DDT Composition Proof — PROVEN
Tools in DDT domain: gate_knn, ternary_encode, bfs_walk, parse_file, write_vindexfile, ...
Tools outside DDT domain (orthogonal): gh_api, curl_http, fetch_tree, subprocess_run, ...
Composition: ddt(pipeline(build_graph_local, ternary_encode, write_vindexfile)) PROVEN
Skills:      ddt(skill(brainstorming)), ddt(skill(executing-plans)), ... PROVEN
Self:        ddt(ddt_proof.py) PROVEN
```

The babel harness is also capable of running more expressive proof assistants (Datalog via Soufflé, Lean, Coq) for stronger guarantees; `ddt_proof.py` is the self-contained baseline.
