#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""CI probe: can a GitHub runner pull ungated open weights from HuggingFace?

Downloads one tiny, well-known, ungated GGUF (no auth token) and reports size.
Exit 0 + printed size on success; non-zero on failure. Non-blocking in CI.
"""
import os
import sys

REPO = os.environ.get("HF_PROBE_REPO", "Qwen/Qwen2.5-0.5B-Instruct-GGUF")
FILE = os.environ.get("HF_PROBE_FILE", "qwen2.5-0.5b-instruct-q4_k_m.gguf")

try:
    from huggingface_hub import hf_hub_download
except Exception as e:  # import failure is itself a probe result
    print(f"huggingface_hub import failed: {e}", file=sys.stderr)
    sys.exit(2)

try:
    path = hf_hub_download(repo_id=REPO, filename=FILE)
except Exception as e:
    print(f"download failed for {REPO}/{FILE}: {e}", file=sys.stderr)
    sys.exit(1)

size = os.path.getsize(path)
print(f"downloaded {REPO}/{FILE} -> {path} ({size} bytes)")
