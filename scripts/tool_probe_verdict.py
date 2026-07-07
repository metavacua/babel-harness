#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Read an ollama /v1/chat/completions response from stdin and classify how the
# model emitted its tool call — the empirical crux of whether the pi agent can
# drive this local model. Verdicts:
#   STRUCTURED — proper tool_calls (pi can execute) — usable
#   TEXT       — tool JSON leaked into the content channel — pi cannot execute
#   NONE       — plain reply, no tool attempt
#   EMPTY / PARSE_FAIL — no usable output
import json, sys

try:
    r = json.load(sys.stdin)
except Exception as e:
    print(f"PARSE_FAIL: {e}")
    sys.exit(0)

msg = (r.get("choices") or [{}])[0].get("message", {}) if isinstance(r, dict) else {}
tc = msg.get("tool_calls")
content = msg.get("content") or ""

if tc:
    name = (tc[0].get("function", {}) or {}).get("name")
    print(f"STRUCTURED ({len(tc)} tool_call(s): {name})")
elif '"name"' in content and ("write" in content or "arguments" in content):
    print("TEXT (tool-call leaked into content — pi cannot execute)")
elif content.strip():
    print("NONE (plain text reply, no tool attempt)")
else:
    print("EMPTY")
