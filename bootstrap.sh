#!/usr/bin/env bash
# Portable entry point for the GoalT MCP server.
#
# Instead of hardcoding a machine-specific Python path (which only works on
# the machine it was written on), this script finds a suitable Python 3.10+
# interpreter at runtime, checks that the server's dependencies are actually
# importable, and then runs the server.
#
# It deliberately does NOT install anything. An earlier version ran
# `pip install -r requirements.txt` on first start, which meant the plugin
# fetched and executed code from the network outside the reviewed repository,
# into whichever interpreter happened to be found first. That is a real
# supply-chain risk for whoever installs the plugin, and it is also
# indistinguishable from a malicious pattern to an automated reviewer.
# Installing the dependencies is now an explicit, one-time step the user runs.

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

# Only what the MCP server and dashboard actually need. matplotlib is used by
# goaltree.visualize, which the server never imports, so it stays optional.
if ! "$PYTHON" -c "import mcp, fastapi, uvicorn, networkx" 2>/dev/null; then
  echo "GoalT's server dependencies aren't installed for $PYTHON." >&2
  echo >&2
  echo "Install them once with:" >&2
  echo >&2
  echo "    $PYTHON -m pip install \"goaltree[mcp]\"" >&2
  echo >&2
  echo "then restart Claude Code. GoalT does not install packages for you." >&2
  exit 1
fi

# Run the server out of the plugin checkout itself (rather than an
# installed copy), so `/plugin update` takes effect immediately. PYTHONPATH
# puts $DIR on the import path so `goaltree` resolves without an install.
exec env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m goaltree.mcp_server
