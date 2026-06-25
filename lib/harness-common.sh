#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
_check_openrouter() {
	curl -sf --max-time 3 "$OPENROUTER_CHECK_URL" >/dev/null 2>&1
}
