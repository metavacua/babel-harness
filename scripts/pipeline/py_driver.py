# SPDX-License-Identifier: AGPL-3.0-or-later
"""python-bindings driver. Contingent: requires `import larql` to work.
Build attempt (one-time, documented):
  cd ~/work/larql-canonical/crates/larql-python
  python3 -m venv ~/work/pyenv && ~/work/pyenv/bin/pip install maturin
  ~/work/pyenv/bin/maturin develop --release
Failure => driver unavailable; recorded as a finding. (2026-07-02 result: build interrupted by session teardown mid-compilation — pyo3 compiled cleanly, so fork issue #221's RUSTSEC prediction was NOT confirmed; see task report and ~/work/artifacts/py-bindings-status.json.)
"""
from __future__ import annotations


def py_bindings_available() -> tuple[bool, str]:
    try:
        import larql  # type: ignore  # noqa: F401
        return True, "import larql ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


class PyBindingsDriver:
    name = "python-bindings"

    def __init__(self, vindex: str):
        ok, reason = py_bindings_available()
        if not ok:
            raise RuntimeError(f"python bindings unavailable: {reason}")
        import larql  # type: ignore
        self._v = larql.Vindex(vindex)  # per crates/larql-python README

    # Same duck-typed surface as CliLqlDriver; implemented iff bindings import.
    def insert_step(self, edge, layer, mode, alpha, patch_path):
        raise NotImplementedError("wire to larql-python mutation API at execution time; "
                                  "record exact API from crates/larql-python/python/larql/")
