# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from scripts.pipeline.contained import contained_cmd, mem_available_mb


def test_contained_cmd_wraps_with_larql_probe():
    got = contained_cmd(["larql", "extract", "x"], mem_mb=2500, cpus=6)
    assert got[:2] == ["larql-probe", "safe"]
    assert "--mem" in got and "2500" in got and "--cpus" in got and "6" in got
    assert got[-3:] == ["larql", "extract", "x"] and "--" in got


def test_mem_available_readable():
    assert mem_available_mb() > 100  # sanity on any live host


def test_run_serial_refuses_when_memory_insufficient(monkeypatch):
    import scripts.pipeline.contained as c
    monkeypatch.setattr(c, "mem_available_mb", lambda: 1000)
    with pytest.raises(MemoryError):
        c.run_serial(["true"], timeout=5, mem_mb=2500)
