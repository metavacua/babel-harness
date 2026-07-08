#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# component_check.py <transcript> — score whether a babel-issue run's DOING
# components FIRED. Function, not quality: it does NOT judge whether the output
# is good, only whether babel *did the things it is built to do* —
#   explored: used a read/search tool (read, or a bash grep/cat/ls/find/head/sed/gh-issue)
#   acted:    used a write/edit tool
# Exits 0 iff both fired (the mechanism functioned); non-zero if a component
# never ran. A GIGO edit that explored+acted passes; confabulation with no tools
# does not — because not-doing-the-thing is the failure we care about first.
import sys, re

data = open(sys.argv[1]).read() if len(sys.argv) > 1 and sys.argv[1] != "-" else sys.stdin.read()

names = set(re.findall(r'"name":"([a-zA-Z_]+)"', data))
bash_cmds = re.findall(r'"command":"([^"]*)"', data)

explored = ("read" in names) or any(
    re.search(r'\b(grep|cat|ls|find|head|sed)\b', c) or "gh issue" in c for c in bash_cmds
)
acted = bool(names & {"write", "edit"})

print(f"explored: {'YES' if explored else 'NO'}")
print(f"acted: {'YES' if acted else 'NO'}")
sys.exit(0 if (explored and acted) else 1)
