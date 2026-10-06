#!/usr/bin/env python3
"""Send a minimal summary of the current tool call to the local GoalT dashboard.

Claude Code hands a `PreToolUse` hook the *entire* tool payload on stdin. For a
`Write` that includes the full contents of the file being written, and for a
`Bash` the complete command line. This script projects the payload down to the
tool name and the file path before anything is sent, so the dashboard shows
e.g. "Editing src/a.py" or "Running: Bash". Everything else, including any
part of a command, is dropped here and never leaves the hook process.

The destination is the dashboard listening on 127.0.0.1, so even the projected
summary stays on this machine. Any failure is swallowed on purpose: the
activity pulse is cosmetic, and must never block, delay or fail a tool call.
"""

import json
import sys
import urllib.request

ENDPOINT = "http://127.0.0.1:8765/hooks/pre-tool-use"

TIMEOUT_SECONDS = 2


def project(payload: dict) -> dict:
    """Reduce a full hook payload to the fields the dashboard actually uses."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    projected: dict = {}

    file_path = tool_input.get("file_path")
    if isinstance(file_path, str):
        projected["file_path"] = file_path

    return {
        "tool_name": payload.get("tool_name", "unknown"),
        "tool_input": projected,
    }


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    body = json.dumps(project(payload)).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Connection refused is the normal case when the dashboard isn't open.
        urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS).close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
