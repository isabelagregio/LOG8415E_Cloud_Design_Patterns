#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
ensure_access
python3 "$ROOT/benchmark/run.py" "$@"
