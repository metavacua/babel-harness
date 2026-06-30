#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
_check_http() {
	curl -sf --max-time 3 "$1" >/dev/null 2>&1
}
_check_openrouter() {
	_check_http "$OPENROUTER_CHECK_URL"
}
