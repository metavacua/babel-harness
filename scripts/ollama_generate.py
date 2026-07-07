#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Shared primitive: call the local Ollama /api/generate ONCE and print the
# response. The one place the "ask the babel-local model" logic lives — used by
# ci_diagnose.sh, ci_fix.sh (and available to other cells). Grounded single-shot
# (temperature 0). Always prints to stdout and exits 0 so callers branch on text.
#
# Usage: ollama_generate.py <url> <model> <prompt> [timeout=120] [default=""]
import json, sys, urllib.request

url, model, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
timeout = int(sys.argv[4]) if len(sys.argv) > 4 else 120
default = sys.argv[5] if len(sys.argv) > 5 else ""
body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                   "options": {"temperature": 0}}).encode()
req = urllib.request.Request(url + "/api/generate", data=body,
                            headers={"Content-Type": "application/json"})
try:
    resp = json.load(urllib.request.urlopen(req, timeout=timeout)).get("response", "")
    print(resp if resp else default)
except Exception as e:
    print(f"(model call failed: {e})")
