#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; ((PASS++))||true; }; bad(){ echo "  FAIL: $1"; ((FAIL++))||true; }

out="$(DIAG_FAKE_MODEL='Root cause: OpenBLAS missing. Fix: apt-get install libopenblas-dev.' \
       DIAG_FAKE_LOG='larql: error while loading shared libraries: libopenblas.so.0' \
       bash "$ROOT/scripts/ci_diagnose.sh" 2>/dev/null)"
echo "$out" | grep -qF 'Root cause: OpenBLAS' && ok "diagnosis includes the model's analysis" || bad "analysis missing"
echo "$out" | grep -qiE '## .*diagnos' && ok "output is a markdown section" || bad "no markdown heading"
echo "== $PASS passed, $FAIL failed =="; [ "$FAIL" -eq 0 ]
