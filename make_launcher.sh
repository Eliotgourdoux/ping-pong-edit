#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ROOT/make_launcher.py" "$@"
