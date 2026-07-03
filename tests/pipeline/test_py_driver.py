# SPDX-License-Identifier: AGPL-3.0-or-later
from scripts.pipeline.py_driver import py_bindings_available

def test_availability_probe_returns_tuple():
    ok, reason = py_bindings_available()
    assert isinstance(ok, bool) and isinstance(reason, str)
