# SPDX-License-Identifier: AGPL-3.0-or-later
"""Containment + serial-execution helpers. HARD POLICY: model-loading commands
must go through contained_cmd (larql-probe safe wrapper). run_serial adds an
flock (never two model processes) and a pre-mmap MemAvailable check (#239
mitigation lives here because the binary lacks it).

Bug fix vs. the plan draft: the MemAvailable check must run AFTER the flock is
acquired, not before. Checking before the lock races a concurrent model job
that is (a) still holding RAM it will free on exit, causing a false refusal
while a slot is about to open up, and (b) free to allocate more RAM between
our check and our actual mmap once we get the lock, making the check stale
exactly when it matters (no swap => an uncontained load crashes the host).
Moving the check inside the `with` block, immediately before subprocess.run,
keeps it a true pre-mmap check for the process about to run.
"""
from __future__ import annotations

import fcntl
import subprocess

LOCK_PATH = "/tmp/larql-pipeline.lock"
HEADROOM_MB = 500


def contained_cmd(cmd: list[str], mem_mb: int = 2500, cpus: int = 6) -> list[str]:
    return ["larql-probe", "safe", "--mem", str(mem_mb), "--cpus", str(cpus),
            "--", *cmd]


def mem_available_mb() -> int:
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable not found")


def run_serial(cmd: list[str], timeout: int, mem_mb: int = 2500, cpus: int = 6,
               **kw) -> subprocess.CompletedProcess:
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # blocks until any other model task finishes
        avail = mem_available_mb()
        if avail < mem_mb + HEADROOM_MB:
            raise MemoryError(f"MemAvailable {avail}MB < required {mem_mb + HEADROOM_MB}MB")
        return subprocess.run(contained_cmd(cmd, mem_mb, cpus), timeout=timeout,
                              capture_output=True, text=True, **kw)
