#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Acceptance spec for scripts/merge_ladder.sh — the deterministic merge-progress
# ladder babel-issue.yml uses to decide whether an autonomous babel run earned a
# PR. Written RED-first (systematic-debugging Phase 4): the 2026-07-08 #19 run
# proved the inline ladder had no rejection power — babel wrote an unrelated
# untracked src/ blob and still "PASSed" every rung. The oracle this suite
# encodes: a PR requires touching a DECLARED TARGET file, and bash -n must see
# files INSIDE untracked dirs (the `?? src/` hole).
#
# Contract under test:
#   env TARGETS_FILE (declared target paths, one/line), LADDER_TESTS ("" = skip)
#   stdout: ladder markdown. Exit: 0 target hit / 10 no change / 11 no target hit.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; LADDER="$ROOT/scripts/merge_ladder.sh"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }; bad(){ echo "  FAIL: $1: $2"; ((FAIL++))||true; }
contains(){ echo "$3" | grep -qFe "$2" && ok "$1" || bad "$1" "missing [$2] in: $(echo "$3" | head -8)"; }

# fresh_repo — temp git repo with one committed target + one bystander file.
# The targets file lives OUTSIDE the repo (like /tmp/targets.txt in CI does).
fresh_repo() {
  T="$(mktemp -d)"; mkdir "$T/repo"; cd "$T/repo" || exit 1
  git init -q; git config user.email t@t.test; git config user.name t
  mkdir -p bin tests
  printf '#!/usr/bin/env bash\necho ok\n' > bin/target-file
  echo bystander > other.txt
  git add -A; git commit -qm init
  echo "bin/target-file" > "$T/targets.txt"
}
run_ladder() { OUT="$(TARGETS_FILE="$T/targets.txt" LADDER_TESTS="${LT-}" bash "$LADDER" 2>/dev/null)"; RC=$?; }

echo "== test-merge-ladder (oracle: PR only when a DECLARED TARGET moved) =="

# 1. Clean tree -> exit 10, rung 0 FAIL.
fresh_repo; run_ladder
[ "$RC" = "10" ] && ok "clean tree exits 10 (honest halt)" || bad "clean tree exits 10" "rc=$RC"
contains "clean tree reports rung 0 FAIL" "rung 0 (touched a file): **FAIL**" "$OUT"

# 2. Non-target change only -> exit 11: rung 0 passes, target oracle FAILS.
fresh_repo; echo y >> other.txt; run_ladder
[ "$RC" = "11" ] && ok "non-target change exits 11 (no PR)" || bad "non-target change exits 11" "rc=$RC"
contains "rung 0 passes on any change" "rung 0 (touched a file): **PASS**" "$OUT"
contains "target oracle FAILS on non-target change" "rung 1 (touched a DECLARED TARGET): **FAIL**" "$OUT"

# 3. THE 2026-07-08 FAILURE MODE: untracked junk dir, syntax-broken .sh inside.
#    Must exit 11 AND the lint rung must see the file INSIDE the dir.
fresh_repo; mkdir -p src; printf 'if then fi (\n' > src/blob.sh; run_ladder
[ "$RC" = "11" ] && ok "untracked junk dir exits 11 (would NOT have PR'd the #19 blob)" || bad "untracked junk dir exits 11" "rc=$RC"
contains "changed-file list resolves INTO untracked dirs" "src/blob.sh" "$OUT"
contains "bash -n sees the file inside the untracked dir" "rung 2 (bash -n clean): **FAIL**" "$OUT"

# 4. Target touched -> exit 0, oracle names the target.
fresh_repo; printf 'true\n' >> bin/target-file; run_ladder
[ "$RC" = "0" ] && ok "target change exits 0 (PR-worthy)" || bad "target change exits 0" "rc=$RC"
contains "oracle PASS names the target" "rung 1 (touched a DECLARED TARGET): **PASS**" "$OUT"
contains "oracle lists which target moved" "bin/target-file" "$OUT"

# 5. Target touched but syntax-broken -> still exit 0 (operator gates), rung 2 FAIL reported.
fresh_repo; printf 'if then fi (\n' >> bin/target-file; run_ladder
[ "$RC" = "0" ] && ok "broken-but-on-target still exits 0 (ladder reports, operator gates)" || bad "broken-but-on-target exits 0" "rc=$RC"
contains "rung 2 reports the syntax failure" "rung 2 (bash -n clean): **FAIL**" "$OUT"

# 6. Missing TARGETS_FILE -> oracle cannot pass (exit 11 when changes exist).
fresh_repo; rm "$T/targets.txt"; echo y >> other.txt; run_ladder
[ "$RC" = "11" ] && ok "missing targets file cannot license a PR" || bad "missing targets file exits 11" "rc=$RC"

# 6b. #19-class basename dump: a file named like the target's BASENAME at the
#     wrong path must NOT satisfy the oracle (kills the grep -x/-F mutants:
#     substring matching would count `target-file` against `bin/target-file`).
fresh_repo; echo junk > target-file; run_ladder
[ "$RC" = "11" ] && ok "basename at wrong path does not satisfy the oracle" || bad "basename-at-root exits 11" "rc=$RC"
contains "basename-at-root: oracle still FAILs" "rung 1 (touched a DECLARED TARGET): **FAIL**" "$OUT"

# 6c. Vacuous-lint honesty: when no changed file is a shell file, rung 2 must
#     say SKIP, not claim a PASS that linted nothing.
fresh_repo; echo y >> other.txt; run_ladder
contains "rung 2 reports SKIP when nothing was lintable" "rung 2 (bash -n clean): **SKIP**" "$OUT"

# 7. Rung 3 runs the given suites and reports per-suite verdicts.
fresh_repo; printf 'true\n' >> bin/target-file
printf '#!/usr/bin/env bash\nexit 0\n' > tests/pass.bash
printf '#!/usr/bin/env bash\nexit 1\n' > tests/fail.bash
LT="tests/pass.bash tests/fail.bash" run_ladder
contains "per-suite PASS line" "tests/pass.bash: PASS" "$OUT"
contains "per-suite FAIL line" "tests/fail.bash: FAIL" "$OUT"
contains "rung 3 FAIL when any suite fails" "rung 3 (certificate suites green): **FAIL**" "$OUT"
LT="tests/pass.bash" run_ladder
contains "rung 3 PASS when all suites pass" "rung 3 (certificate suites green): **PASS**" "$OUT"

echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
