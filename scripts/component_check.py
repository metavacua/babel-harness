#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# component_check.py <transcript> — score whether a babel-issue run's three DOING
# components FIRED, each detected from babel's OWN tool calls. Function, not
# quality: it does not judge whether the output is good, only whether babel did
# the things it is built to do —
#   read_issue: read the issue itself   (a bash `gh issue ...` call)
#   explored:   read/searched the repo  (read tool, or bash grep/cat/ls/find/head/sed)
#   acted:      wrote/edited a file      (write/edit tool)
# Exits 0 iff all three fired; non-zero if any component never ran. A GIGO edit
# that read+explored+acted passes; confabulation with no tools does not —
# because not-doing-the-thing is the failure we care about first.
import sys, re

data = open(sys.argv[1]).read() if len(sys.argv) > 1 and sys.argv[1] != "-" else sys.stdin.read()

names = set(re.findall(r'"name":"([a-zA-Z_]+)"', data))
bash_cmds = re.findall(r'"command":"([^"]*)"', data)

read_issue = any("gh issue" in c for c in bash_cmds)
explored = ("read" in names) or any(re.search(r'\b(grep|cat|ls|find|head|sed)\b', c) for c in bash_cmds)
acted = bool(names & {"write", "edit"})

print(f"read_issue: {'YES' if read_issue else 'NO'}")
print(f"explored: {'YES' if explored else 'NO'}")
print(f"acted: {'YES' if acted else 'NO'}")
sys.exit(0 if (read_issue and explored and acted) else 1)
