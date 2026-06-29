# Repo-to-Patch Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/build_repo_patch.py` — a script that reads Vindexfile `INSERT "e","r","t"` directives (Form 1), translates them to LQL `INSERT INTO EDGES ... MODE KNN` statements (Form 2), runs them through `larql lql` in a `BEGIN PATCH / SAVE PATCH` session, and emits a persistent `.vlp` patch file; then pre-compute `.vlp` fixtures for babel-harness and chrishayuk/larql at pinned commits.

**Architecture:** `extract-graph.py` already produces the Vindexfile (Form 1 INSERT lines). `build_repo_patch.py` reads those lines, assembles a multi-statement LQL batch (`USE / BEGIN PATCH / N×INSERT INTO EDGES MODE KNN / SAVE PATCH`), and dispatches it as a single `subprocess.run(["larql", "lql", batch])` call — no shell, no quoting issues. `larql lql` calls `run_batch()` which splits on semicolons and executes each statement in a shared session. The resulting `.vlp` contains `insert_knn` ops with real `key_vector_b64` values (forward-pass residuals when weights are available, embedding-lookup vectors for browse-only). The `.vlp` is committed as a test fixture — no GitHub API calls at test time.

**Tech Stack:** Python 3.11+, `larql` binary (`~/larql/target/release/larql`), `smollm2-360m.vindex` (full weights confirmed: `embeddings.bin`, `up/down/attn_weights.bin`), existing `scripts/extract-graph.py`, existing `scripts/github_graph.py`, `pytest`.

## Global Constraints

- No GitHub API POST/PATCH/DELETE calls — all GitHub access is GET-only (read-only)
- `chrishayuk/larql` is read-only — no commits, branches, PRs, or issues on that repo
- All commits go to `metavacua/babel-harness` only
- Pinned commits: babel-harness @ `94485d4`, chrishayuk/larql @ `4a120baf`
- `LARQL_BIN` default: `~/larql/target/release/larql` (release build required for timing)
- `BASE_VINDEX` default: `~/larql-vindexes/smollm2-360m.vindex`
- Tests that require `larql` binary or base vindex must skip when unavailable (use `pytest.mark.skipif`)
- No new dependencies beyond stdlib + pytest — `build_repo_patch.py` must run as plain `python3`
- **WALK vs INFER semantics** (empirically confirmed):
  - `WALK "entity" TOP k` = pure vindex gate-vector scan. Does NOT read the KnnStore.
    Cannot be used to verify KNN inserts. Returns feature-level token predictions only.
  - `INFER "prompt" TOP k` = full forward pass + KNN override at post-logits.
    DOES read the KnnStore. Use this to verify that KNN inserts are retrievable.
  - All roundtrip verification tests must use INFER, not WALK.
- **Timing** (empirically confirmed on this machine):
  - Cold-start `larql lql` with INSERT INTO EDGES MODE KNN (weights not in OS page cache):
    ~17m for 3 inserts (dominated by mmap page faults loading weights from disk)
  - Warm-cache (weights already in OS page cache): ~42s for 3 inserts (~14s/triple)
  - INFER query with warm cache: ~15s per call
  - For 95 triples in one batch on a warm machine: ~22 min; cold: add ~17m penalty
  - `@needs_larql` integration tests have long timeouts: see per-test `timeout=` values
- The four INSERT forms must remain clearly distinct in all comments and variable names:
  - Form 1: `INSERT "e","r","t"` (Vindexfile directive, stub per #242)
  - Form 2: `INSERT INTO EDGES (...) VALUES (...) MODE KNN` (LQL session, working)
  - Form 3: `INSERT INTO EDGES (...) VALUES (...) MODE COMPOSE` (LQL session, working)
  - Form 4: `BEGIN PATCH / SAVE PATCH / APPLY PATCH / COMPILE INTO VINDEX` (patch session)
- `build_repo_patch.py` uses Form 2 statements inside a Form 4 session
- No shell=True in subprocess calls

---

### Task 1: Pure-Python utilities — parse and translate (no larql required)

**Files:**
- Create: `scripts/build_repo_patch.py`
- Create: `tests/test_build_repo_patch.py`

**Interfaces:**
- Produces:
  - `parse_vindexfile_inserts(text: str) -> list[tuple[str, str, str]]`
  - `build_lql_batch(triples: list[tuple[str, str, str]], base_vindex: str, output_vlp: str) -> str`

- [ ] **Step 1: Write the failing tests (pure-Python functions, no subprocess)**

```python
# tests/test_build_repo_patch.py
import pathlib, sys, textwrap
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
from build_repo_patch import parse_vindexfile_inserts, build_lql_batch


def test_parse_extracts_insert_triples():
    text = textwrap.dedent("""\
        FROM /some/base.vindex
        # comment
        EXPOSE browse
        INSERT "coding-agent", "calls", "_check_larql"
        INSERT "_run_goose_larql", "calls", "_run_goose_call"
    """)
    result = parse_vindexfile_inserts(text)
    assert result == [
        ("coding-agent", "calls", "_check_larql"),
        ("_run_goose_larql", "calls", "_run_goose_call"),
    ]


def test_parse_skips_non_insert_lines():
    text = "FROM base.vindex\nEXPOSE browse\nINSERT \"a\", \"b\", \"c\"\n"
    result = parse_vindexfile_inserts(text)
    assert len(result) == 1 and result[0] == ("a", "b", "c")


def test_parse_empty_vindexfile_returns_empty():
    assert parse_vindexfile_inserts("FROM base.vindex\n# no inserts\n") == []


def test_build_lql_batch_structure():
    triples = [("e1", "calls", "e2"), ("e3", "defines", "e4")]
    lql = build_lql_batch(triples, "/base.vindex", "/out.vlp")
    assert 'USE "/base.vindex";' in lql
    assert 'BEGIN PATCH "/out.vlp";' in lql
    assert 'VALUES ("e1", "calls", "e2") MODE KNN;' in lql
    assert 'VALUES ("e3", "defines", "e4") MODE KNN;' in lql
    assert lql.strip().endswith("SAVE PATCH;")


def test_build_lql_batch_uses_into_edges_syntax():
    # Must be Form 2 (LQL session INSERT), NOT Form 1 (Vindexfile INSERT)
    lql = build_lql_batch([("a", "b", "c")], "/base", "/out.vlp")
    assert "INSERT INTO EDGES" in lql
    assert 'INSERT "' not in lql  # Form 1 syntax must NOT appear


def test_build_lql_batch_escapes_double_quotes():
    triples = [('say "hi"', "calls", 'say "bye"')]
    lql = build_lql_batch(triples, "/base", "/out.vlp")
    # Embedded double-quotes escaped as \"
    assert r'say \"hi\"' in lql
    assert r'say \"bye\"' in lql


def test_build_lql_batch_one_insert_per_triple():
    triples = [("a", "b", "c"), ("d", "e", "f"), ("g", "h", "i")]
    lql = build_lql_batch(triples, "/base", "/out.vlp")
    assert lql.count("INSERT INTO EDGES") == 3
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ~/babel-harness
python3 -m pytest tests/test_build_repo_patch.py -k "parse or build_lql" -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'build_repo_patch'`

- [ ] **Step 3: Implement `scripts/build_repo_patch.py` — pure-Python part only**

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
build_repo_patch.py — Translate Vindexfile INSERT directives (Form 1) into a
persistent .vlp KNN patch via LQL session INSERT INTO EDGES MODE KNN (Form 2
inside a Form 4 patch session).

Background:
  Vindexfile INSERT "e","r","t" is a stub (larql-to-sparql #242) — it writes
  empty gate vectors and produces no retrievable knowledge.  This script works
  around #242 by re-expressing the same triples as LQL session INSERTs which
  DO compute real key_vector_b64 values (forward-pass residuals when weights
  are available, embedding-lookup vectors for browse-only vindexes).

Usage:
    python3 scripts/build_repo_patch.py \
        --base-vindex ~/larql-vindexes/smollm2-360m.vindex \
        --output tests/fixtures/babel-harness-94485d4.vlp

    # From a remote repo (uses github_graph.py, GET-only):
    python3 scripts/build_repo_patch.py \
        --remote chrishayuk/larql@4a120baf \
        --base-vindex ~/larql-vindexes/smollm2-360m.vindex \
        --output tests/fixtures/larql-4a120baf.vlp
"""
from __future__ import annotations
import argparse, json, pathlib, re, subprocess, sys

LARQL_BIN_DEFAULT = pathlib.Path.home() / "larql/target/release/larql"
GITHUB_GRAPH_SCRIPT = pathlib.Path(__file__).parent / "github_graph.py"

# Matches: INSERT "entity", "relation", "target"
# This is the Form 1 Vindexfile INSERT syntax (stub, per #242).
_FORM1_RE = re.compile(
    r'^INSERT\s+"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"',
    re.MULTILINE,
)


def parse_vindexfile_inserts(text: str) -> list[tuple[str, str, str]]:
    """Extract (entity, relation, target) triples from Vindexfile Form 1 INSERT lines."""
    return [(m.group(1), m.group(2), m.group(3)) for m in _FORM1_RE.finditer(text)]


def _esc(s: str) -> str:
    """Escape double-quotes inside an LQL double-quoted string literal."""
    return s.replace('"', '\\"')


def build_lql_batch(
    triples: list[tuple[str, str, str]],
    base_vindex: str,
    output_vlp: str,
) -> str:
    """
    Build a multi-statement LQL batch that:
      1. Loads the base vindex (USE)
      2. Opens a patch recording session (BEGIN PATCH)
      3. Inserts each triple via Form 2 LQL INSERT INTO EDGES MODE KNN
      4. Saves the .vlp file (SAVE PATCH)

    The resulting string is passed verbatim to `larql lql`.
    larql's run_batch() splits on semicolons and executes each statement
    in a shared Session, so the USE/BEGIN PATCH state carries across.
    """
    lines = [
        f'USE "{_esc(base_vindex)}";',
        f'BEGIN PATCH "{_esc(output_vlp)}";',
    ]
    for entity, relation, target in triples:
        lines.append(
            f'INSERT INTO EDGES (entity, relation, target) '
            f'VALUES ("{_esc(entity)}", "{_esc(relation)}", "{_esc(target)}") MODE KNN;'
        )
    lines.append("SAVE PATCH;")
    return "\n".join(lines)
```

- [ ] **Step 4: Run pure-Python tests to verify they pass**

```bash
cd ~/babel-harness
python3 -m pytest tests/test_build_repo_patch.py -k "parse or build_lql" -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/babel-harness
git add scripts/build_repo_patch.py tests/test_build_repo_patch.py
git commit -m "feat: build_repo_patch.py — parse Vindexfile Form 1, emit LQL Form 2 batch"
```

---

### Task 2: CLI — subprocess dispatch and .vlp validation

**Files:**
- Modify: `scripts/build_repo_patch.py` (add `main()` and subprocess logic)
- Modify: `tests/test_build_repo_patch.py` (add CLI and integration tests)

**Interfaces:**
- Consumes: `parse_vindexfile_inserts`, `build_lql_batch` from Task 1
- Produces: CLI `build_repo_patch.py --vindexfile PATH --base-vindex PATH --output PATH [--dry-run] [--larql-bin PATH]`
- Produces: exit 0 + `.vlp` file on success; exit 1 + stderr message on any failure

- [ ] **Step 1: Write failing CLI tests**

Add to `tests/test_build_repo_patch.py`:

```python
import os, subprocess, json, textwrap, pathlib, sys
import pytest

SCRIPTS = pathlib.Path(__file__).parent.parent / "scripts"
LARQL_BIN = pathlib.Path.home() / "larql/target/release/larql"
BASE_VINDEX = pathlib.Path.home() / "larql-vindexes/smollm2-360m.vindex"

needs_larql = pytest.mark.skipif(
    not LARQL_BIN.exists() or not BASE_VINDEX.exists(),
    reason="larql binary or smollm2-360m.vindex not available",
)


def _run_builder(vf_text: str, tmp_path: pathlib.Path, extra_args=()) -> tuple[pathlib.Path, subprocess.CompletedProcess]:
    vf = tmp_path / "Vindexfile"
    vf.write_text(vf_text)
    out = tmp_path / "out.vlp"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_repo_patch.py"),
         "--vindexfile", str(vf),
         "--base-vindex", str(BASE_VINDEX),
         "--output", str(out),
         *extra_args],
        capture_output=True, text=True,
    )
    return out, proc


def test_dry_run_prints_lql_does_not_create_vlp(tmp_path):
    vf = tmp_path / "Vindexfile"
    vf.write_text('INSERT "a", "calls", "b"\n')
    out = tmp_path / "out.vlp"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_repo_patch.py"),
         "--vindexfile", str(vf),
         "--base-vindex", "/dummy/path",
         "--output", str(out),
         "--dry-run"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "INSERT INTO EDGES" in proc.stdout
    assert "MODE KNN" in proc.stdout
    assert "1 triples" in proc.stdout
    assert not out.exists(), ".vlp must NOT be created in --dry-run mode"


def test_no_inserts_exits_nonzero(tmp_path):
    vf = tmp_path / "Vindexfile"
    vf.write_text("FROM base.vindex\nEXPOSE browse\n")
    out = tmp_path / "out.vlp"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_repo_patch.py"),
         "--vindexfile", str(vf),
         "--base-vindex", "/dummy",
         "--output", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "no INSERT directives" in proc.stderr


def test_missing_vindexfile_exits_nonzero(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_repo_patch.py"),
         "--vindexfile", str(tmp_path / "NoSuchFile"),
         "--base-vindex", "/dummy",
         "--output", str(tmp_path / "out.vlp")],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "not found" in proc.stderr


@needs_larql
def test_vlp_is_valid_json_with_insert_knn_ops(tmp_path):
    vf_text = textwrap.dedent("""\
        INSERT "gate_knn", "calls", "entity_walk"
        INSERT "gate_knn", "calls", "vindex"
        INSERT "entity_walk", "calls", "gate_knn"
    """)
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, f"builder failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert out.exists(), ".vlp file not created"
    patch = json.loads(out.read_text())
    ops = patch["operations"]
    assert len(ops) == 3
    for op in ops:
        assert op["op"] == "insert_knn", f"expected insert_knn, got {op['op']}"
        assert "key_vector_b64" in op and op["key_vector_b64"], "key_vector_b64 missing or empty"


@needs_larql
def test_vlp_insert_knn_op_has_expected_fields(tmp_path):
    vf_text = 'INSERT "coding-agent", "calls", "_check_larql"\n'
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, proc.stderr
    op = json.loads(out.read_text())["operations"][0]
    assert op["op"] == "insert_knn"
    assert op["entity"] == "coding-agent"
    assert op["relation"] == "calls"
    assert op["target"] == "_check_larql"
    assert isinstance(op["layer"], int) and op["layer"] >= 0
    assert isinstance(op["target_id"], int) and op["target_id"] > 0
    assert len(op["key_vector_b64"]) > 0


@needs_larql
def test_vlp_roundtrip_infer_returns_inserted_entity(tmp_path):
    """Built .vlp can be applied and INFER returns the inserted target via KNN override.

    WALK cannot be used here: WALK is a pure gate-vector scan that does not read
    the KnnStore. INFER does a full forward pass and then checks the KnnStore at
    post-logits (source='knn_override/post_logits'), so it correctly surfaces
    entities inserted via INSERT INTO EDGES MODE KNN.

    Timing: warm-cache ~43s (28s build + 15s infer); cold ~17+ min.
    """
    vf_text = textwrap.dedent("""\
        INSERT "gate_knn", "calls", "entity_walk"
        INSERT "entity_walk", "calls", "vindex"
    """)
    out, proc = _run_builder(vf_text, tmp_path)
    assert proc.returncode == 0, proc.stderr

    infer_lql = (
        f'USE "{BASE_VINDEX}"; '
        f'APPLY PATCH "{out}"; '
        f'INFER "The calls of gate_knn is" TOP 5;'
    )
    infer = subprocess.run(
        [str(LARQL_BIN), "lql", infer_lql],
        capture_output=True, text=True, timeout=300,
    )
    assert infer.returncode == 0, f"INFER failed:\n{infer.stderr}"
    assert "entity_walk" in infer.stdout, (
        f"INFER did not return 'entity_walk' after APPLY PATCH.\n"
        f"Expected: entity_walk at 100%, source=knn_override/post_logits\n"
        f"stdout: {infer.stdout}\nstderr: {infer.stderr}"
    )
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ~/babel-harness
python3 -m pytest tests/test_build_repo_patch.py -k "cli or dry_run or vlp or roundtrip or nonzero or missing" -v 2>&1 | head -30
```

Expected: errors — `main()` not defined, process exits non-zero on all attempts.

- [ ] **Step 3: Implement `main()` in `scripts/build_repo_patch.py`**

Append to `scripts/build_repo_patch.py`:

```python
def _run_larql_lql(larql_bin: str, lql: str) -> tuple[int, str]:
    """Run `larql lql BATCH` via subprocess (no shell). Returns (returncode, combined output)."""
    result = subprocess.run(
        [larql_bin, "lql", lql],
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout + result.stderr


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate Vindexfile Form-1 INSERT directives into a .vlp KNN patch.")
    parser.add_argument(
        "--vindexfile", default="Vindexfile", metavar="PATH",
        help="Path to Vindexfile containing Form-1 INSERT directives (default: ./Vindexfile)")
    parser.add_argument(
        "--base-vindex", required=True, dest="base_vindex", metavar="PATH",
        help="Base .vindex directory (e.g. ~/larql-vindexes/smollm2-360m.vindex)")
    parser.add_argument(
        "--output", required=True, metavar="PATH",
        help="Output .vlp file path")
    parser.add_argument(
        "--larql-bin", default=str(LARQL_BIN_DEFAULT), dest="larql_bin", metavar="PATH",
        help=f"Path to larql binary (default: {LARQL_BIN_DEFAULT})")
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Print LQL batch to stdout without running larql")
    args = parser.parse_args()

    vf_path = pathlib.Path(args.vindexfile)
    if not vf_path.exists():
        print(f"error: Vindexfile not found: {vf_path}", file=sys.stderr)
        sys.exit(1)

    triples = parse_vindexfile_inserts(vf_path.read_text())
    if not triples:
        print("error: no INSERT directives found in Vindexfile", file=sys.stderr)
        sys.exit(1)

    output = pathlib.Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    lql = build_lql_batch(triples, args.base_vindex, str(output))

    if args.dry_run:
        print(lql)
        print(f"\n-- {len(triples)} triples would be inserted")
        return

    print(f"build_repo_patch: {len(triples)} triples → {output}", file=sys.stderr)
    rc, out = _run_larql_lql(args.larql_bin, lql)
    for line in out.splitlines():
        print(line, file=sys.stderr)

    if rc != 0:
        print(f"error: larql lql failed (exit {rc})", file=sys.stderr)
        sys.exit(1)

    if not output.exists():
        print(f"error: larql lql exited 0 but {output} was not created", file=sys.stderr)
        sys.exit(1)

    try:
        patch = json.loads(output.read_text())
        ops = patch.get("operations", [])
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"error: output .vlp is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    knn_count = sum(1 for op in ops if op.get("op") == "insert_knn")
    print(f"build_repo_patch: ok — {knn_count}/{len(triples)} insert_knn ops in {output.name}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

```bash
cd ~/babel-harness
python3 -m pytest tests/test_build_repo_patch.py -v
```

Expected: all pure-Python tests pass; `@needs_larql` tests skip if binary unavailable, pass otherwise. `test_vlp_roundtrip_infer_returns_inserted_entity` must show "entity_walk" in INFER output with `source=knn_override/post_logits`. With warm OS cache (weights already loaded), roundtrip test takes ~45s; cold-start adds ~17m.

- [ ] **Step 5: Commit**

```bash
cd ~/babel-harness
git add scripts/build_repo_patch.py tests/test_build_repo_patch.py
git commit -m "feat: build_repo_patch.py — CLI dispatch, subprocess to larql lql, .vlp validation"
```

---

### Task 3: `--remote` support for direct GitHub repo ingestion

Allows building a larql-only `.vlp` without running `extract-graph.py` (which always merges babel-harness). Calls `github_graph.py --output lql` (GET-only, no auth required for public repos) and treats its output as Vindexfile Form-1 INSERT lines.

**Files:**
- Modify: `scripts/build_repo_patch.py`
- Modify: `tests/test_build_repo_patch.py`

**Interfaces:**
- Consumes: `github_graph.py --repo OWNER/REPO --ref REF --output lql` stdout (Form-1 INSERT lines)
- Produces: `--remote OWNER/REPO[@REF]` CLI flag that replaces `--vindexfile` for remote repos
- Note: `--remote` and `--vindexfile` are mutually exclusive; one is required

- [ ] **Step 1: Write failing test for `--remote` mode**

Add to `tests/test_build_repo_patch.py`:

```python
GITHUB_GRAPH = pathlib.Path(__file__).parent.parent / "scripts" / "github_graph.py"
needs_network = pytest.mark.skipif(
    not GITHUB_GRAPH.exists(),
    reason="github_graph.py not available",
)

@needs_network
@needs_larql
def test_remote_flag_builds_vlp_from_github(tmp_path):
    """--remote fetches metavacua/babel-harness graph and builds a .vlp."""
    out = tmp_path / "remote.vlp"
    # Use metavacua/babel-harness (public, metavacua-owned, GET-only)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_repo_patch.py"),
         "--remote", "metavacua/babel-harness@94485d4",
         "--base-vindex", str(BASE_VINDEX),
         "--output", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"--remote build failed:\n{proc.stderr}"
    assert out.exists()
    ops = json.loads(out.read_text())["operations"]
    assert len(ops) > 0, "no insert_knn ops in remote-built .vlp"
    assert all(op["op"] == "insert_knn" for op in ops)


def test_remote_and_vindexfile_mutually_exclusive(tmp_path):
    vf = tmp_path / "Vindexfile"
    vf.write_text('INSERT "a", "calls", "b"\n')
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_repo_patch.py"),
         "--vindexfile", str(vf),
         "--remote", "owner/repo",
         "--base-vindex", "/dummy",
         "--output", str(tmp_path / "out.vlp")],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "mutually exclusive" in proc.stderr or proc.returncode != 0
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ~/babel-harness
python3 -m pytest tests/test_build_repo_patch.py -k "remote" -v 2>&1 | head -20
```

Expected: `test_remote_flag_builds_vlp_from_github` fails (--remote not implemented); mutual-exclusion test fails.

- [ ] **Step 3: Add `--remote` to `main()` in `scripts/build_repo_patch.py`**

Replace the argument-parsing and triple-loading block in `main()`:

```python
def _fetch_remote_triples(owner_repo_ref: str, github_graph_script: str) -> list[tuple[str, str, str]]:
    """
    Fetch Form-1 INSERT lines from a public GitHub repo via github_graph.py.
    GET-only — does not write to the remote repo.
    """
    if "@" in owner_repo_ref:
        repo, ref = owner_repo_ref.split("@", 1)
    else:
        repo, ref = owner_repo_ref, "main"

    result = subprocess.run(
        [sys.executable, github_graph_script,
         "--repo", repo, "--ref", ref, "--output", "lql"],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"github_graph.py failed for {owner_repo_ref}:\n{result.stderr[:500]}"
        )
    return parse_vindexfile_inserts(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate Vindexfile Form-1 INSERT directives into a .vlp KNN patch.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--vindexfile", metavar="PATH",
        help="Path to Vindexfile containing Form-1 INSERT directives")
    source_group.add_argument(
        "--remote", metavar="OWNER/REPO[@REF]",
        help="Fetch graph from a public GitHub repo (GET-only). "
             "Example: --remote chrishayuk/larql@4a120baf")
    parser.add_argument(
        "--base-vindex", required=True, dest="base_vindex", metavar="PATH",
        help="Base .vindex directory")
    parser.add_argument(
        "--output", required=True, metavar="PATH",
        help="Output .vlp file path")
    parser.add_argument(
        "--larql-bin", default=str(LARQL_BIN_DEFAULT), dest="larql_bin", metavar="PATH",
        help=f"larql binary path (default: {LARQL_BIN_DEFAULT})")
    parser.add_argument(
        "--github-graph-script", default=str(GITHUB_GRAPH_SCRIPT),
        dest="github_graph_script", metavar="PATH",
        help=f"Path to github_graph.py (default: {GITHUB_GRAPH_SCRIPT})")
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Print LQL batch to stdout without running larql")
    args = parser.parse_args()

    if args.remote:
        print(f"build_repo_patch: fetching graph from github://{args.remote}", file=sys.stderr)
        try:
            triples = _fetch_remote_triples(args.remote, args.github_graph_script)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        vf_path = pathlib.Path(args.vindexfile)
        if not vf_path.exists():
            print(f"error: Vindexfile not found: {vf_path}", file=sys.stderr)
            sys.exit(1)
        triples = parse_vindexfile_inserts(vf_path.read_text())

    if not triples:
        src = args.remote or args.vindexfile
        print(f"error: no INSERT directives found in {src}", file=sys.stderr)
        sys.exit(1)

    output = pathlib.Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    lql = build_lql_batch(triples, args.base_vindex, str(output))

    if args.dry_run:
        print(lql)
        print(f"\n-- {len(triples)} triples would be inserted")
        return

    print(f"build_repo_patch: {len(triples)} triples → {output}", file=sys.stderr)
    rc, out = _run_larql_lql(args.larql_bin, lql)
    for line in out.splitlines():
        print(line, file=sys.stderr)

    if rc != 0:
        print(f"error: larql lql failed (exit {rc})", file=sys.stderr)
        sys.exit(1)

    if not output.exists():
        print(f"error: larql lql exited 0 but {output} was not created", file=sys.stderr)
        sys.exit(1)

    try:
        patch = json.loads(output.read_text())
        ops = patch.get("operations", [])
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"error: output .vlp is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    knn_count = sum(1 for op in ops if op.get("op") == "insert_knn")
    print(f"build_repo_patch: ok — {knn_count}/{len(triples)} insert_knn ops in {output.name}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

```bash
cd ~/babel-harness
python3 -m pytest tests/test_build_repo_patch.py -v
```

Expected: all pure-Python tests pass; `@needs_larql` and `@needs_network` tests skip or pass depending on environment. `test_remote_and_vindexfile_mutually_exclusive` must pass (argparse enforces mutual exclusion automatically).

- [ ] **Step 5: Commit**

```bash
cd ~/babel-harness
git add scripts/build_repo_patch.py tests/test_build_repo_patch.py
git commit -m "feat: build_repo_patch.py — add --remote flag for GitHub repo graph ingestion"
```

---

### Task 4: Pre-compute and commit fixtures

Two fixtures are needed for the oracle test harness (Plan B):
- `tests/fixtures/babel-harness-94485d4.vlp` — babel-harness graph at commit `94485d4`
- `tests/fixtures/larql-4a120baf.vlp` — chrishayuk/larql graph at commit `4a120baf`

**Files:**
- Create: `tests/fixtures/babel-harness-94485d4.vlp`
- Create: `tests/fixtures/larql-4a120baf.vlp`
- Modify: `.gitignore` (if `.vlp` is excluded; check and allow `tests/fixtures/*.vlp`)

**Interfaces:**
- Consumes: `build_repo_patch.py` from Tasks 1–3
- Produces: two committed `.vlp` files consumable by Plan B as `pytest` fixtures

- [ ] **Step 1: Check whether .vlp files are gitignored**

```bash
cd ~/babel-harness
git check-ignore -v tests/fixtures/test.vlp 2>&1
```

If output is non-empty (file is ignored): add `!tests/fixtures/*.vlp` to `.gitignore`.
If output is empty (not ignored): proceed.

- [ ] **Step 2: Generate babel-harness fixture**

The current `Vindexfile` was generated at commit `94485d4` (confirmed: `git log -1` shows `94485d4`).

```bash
cd ~/babel-harness
mkdir -p tests/fixtures

python3 scripts/build_repo_patch.py \
    --vindexfile Vindexfile \
    --base-vindex ~/larql-vindexes/smollm2-360m.vindex \
    --output tests/fixtures/babel-harness-94485d4.vlp

echo "--- triple count ---"
python3 -c "import json; d=json.load(open('tests/fixtures/babel-harness-94485d4.vlp')); print(len(d['operations']), 'ops')"
```

Expected stderr: `build_repo_patch: N triples → .../babel-harness-94485d4.vlp` then `ok — N/N insert_knn ops`.
Expected python3 output: `95 ops` (matches Vindexfile INSERT count from `grep -c ^INSERT Vindexfile`).

- [ ] **Step 3: Verify fixture with INFER**

Note: Use INFER (not WALK). WALK is a pure gate-vector scan that does not read the KnnStore.
INFER does the full forward pass and consults the KnnStore at post-logits — this is what
surfaces KNN-inserted entities. Expect `source=knn_override/post_logits` in the output.
Runtime with warm OS cache: ~15s per query.

```bash
cd ~/babel-harness
LARQL_BIN=~/larql/target/release/larql
BASE_VINDEX=~/larql-vindexes/smollm2-360m.vindex

$LARQL_BIN lql "USE \"$BASE_VINDEX\"; APPLY PATCH \"$(pwd)/tests/fixtures/babel-harness-94485d4.vlp\"; INFER \"coding-agent calls\" TOP 5;"
```

Expected: output includes `_check_larql`, `_larql_cleanup`, `_run_goose_larql`, or similar
babel-harness function names with `source=knn_override/post_logits`. If no KNN override fires,
the fixture is valid but the INFER prompt doesn't activate the right layer-24 residual —
try other prompts like `"coding-agent"` or `"The coding-agent calls"`. Confirm which entities
are in the Vindexfile with `grep "coding-agent" Vindexfile`.

- [ ] **Step 4: Generate larql fixture**

```bash
cd ~/babel-harness

python3 scripts/build_repo_patch.py \
    --remote chrishayuk/larql@4a120baf \
    --base-vindex ~/larql-vindexes/smollm2-360m.vindex \
    --output tests/fixtures/larql-4a120baf.vlp

echo "--- triple count ---"
python3 -c "import json; d=json.load(open('tests/fixtures/larql-4a120baf.vlp')); print(len(d['operations']), 'ops')"
```

Expected: stderr shows fetching from github://chrishayuk/larql@4a120baf, then N triples inserted. The larql repo graph is substantially larger than babel-harness (5807 triples estimated from previous session; `--remote` fetches via github_graph.py which applies its own filtering). Operation count may be lower than the raw triple count due to ternary encoding in github_graph.py.

- [ ] **Step 5: Verify larql fixture with INFER**

Same caveat as Step 3: use INFER to verify KNN-inserted entities, not WALK.

```bash
cd ~/babel-harness
LARQL_BIN=~/larql/target/release/larql
BASE_VINDEX=~/larql-vindexes/smollm2-360m.vindex

$LARQL_BIN lql "USE \"$BASE_VINDEX\"; APPLY PATCH \"$(pwd)/tests/fixtures/larql-4a120baf.vlp\"; INFER \"entity_walk calls\" TOP 5;"
```

Expected: output includes larql-related entity names (`gate_knn`, `vindex`, `walk`, or similar)
with `source=knn_override/post_logits`.

- [ ] **Step 6: Commit fixtures**

```bash
cd ~/babel-harness
git add tests/fixtures/babel-harness-94485d4.vlp tests/fixtures/larql-4a120baf.vlp
git commit -m "feat: pre-computed KNN patch fixtures for babel-harness@94485d4 and larql@4a120baf"
```

If `.gitignore` was modified in Step 1:
```bash
git add .gitignore
git commit -m "chore: allow tests/fixtures/*.vlp in git"
```
(commit this before the fixtures commit)

---

## What this plan does NOT cover (Plan B)

The oracle test harness — the A×B×C evaluation that asks coding-agent to write programs Q that predict target program P's behavior, then compares P and Q's execution — is a separate plan (`2026-06-29-oracle-test-harness.md`). It depends on the fixtures built here.

Plan B's key tasks:
1. A-dimension gate: verify fixture build succeeds and triple count is in expected range
2. B-dimension gate: WALK precision@k against a golden entity set per repo
3. C-dimension oracle: coding-agent writes program Q (test/script/LQL session) predicting P's behavior; oracle runs both and scores

Plan B cannot begin until both `.vlp` fixtures from Task 4 are committed and `test_vlp_roundtrip_walk_returns_inserted_entity` (Task 2 of this plan) passes.
