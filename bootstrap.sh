#!/usr/bin/env bash
# Portable entry point for the GoalT MCP server.
#
# Instead of hardcoding a machine-specific Python path (which only works on
# the machine it was written on), this script finds a suitable Python 3.10+
# interpreter at runtime, and auto-installs GoalT's dependencies the first
# time it runs if they're not already present. This means installing the
# plugin is genuinely just the two `/plugin` commands in the README --
# no manual `git clone` + `pip install` step required.
#
# Runs once per server start; after the first run, dependencies are already
# installed so startup is instant (the import check below is fast).

set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "GoalT requires Python 3.10 or newer, and couldn't find one on your PATH." >&2
  echo "Install Python 3.10+ (e.g. from python.org or 'brew install python@3.12'), then try again." >&2
  exit 1
fi

if ! "$PYTHON" -c "import mcp, fastapi, uvicorn, networkx, matplotlib" 2>/dev/null; then
  "$PYTHON" -m pip install -q -r "$DIR/requirements.txt" 1>&2
fi

exec "$PYTHON" "$DIR/mcp_server.py"
