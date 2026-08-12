"""Integration tests for goaltree.mcp_server -- uses a real MCP client talking to
a real subprocess over stdio, the same way Claude Code does. Slower than
the unit tests but tests the thing people actually use.

Run with: pytest tests/test_mcp_server.py -v
(requires the `mcp` package, already a project dependency)
"""

import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def _run_session(project_dir, calls):
    """Start a fresh goaltree.mcp_server subprocess, run `calls` (a list of
    (tool_name, args) tuples) against it, and return the list of results.

    Uses sys.executable rather than a bare "python3" so the subprocess is
    guaranteed to be the same interpreter running pytest -- on machines
    with more than one Python installed, a bare "python3" can resolve to
    a different one that doesn't have the project's dependencies.
    """
    params = StdioServerParameters(command=sys.executable, args=["-m", "goaltree.mcp_server"], cwd=REPO_ROOT)
    results = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool_name, args in calls:
                r = await session.call_tool(tool_name, args)
                results.append(r.content[0].text)
    return results


@pytest.fixture
def project_dir(tmp_path):
    return str(tmp_path)


@pytest.mark.asyncio
async def test_create_tree_and_add_goal(project_dir):
    results = await _run_session(project_dir, [
        ("create_tree", {"root_label": "Test", "project_root": project_dir}),
        ("add_goal", {"id": "a", "label": "A", "parents": ["root"]}),
        ("list_priorities", {}),
    ])
    assert "Created tree" in results[0]
    assert "Added 'A'" in results[1]
    assert "A" in results[2] and "root" in results[2].lower() or "Test" in results[2]


@pytest.mark.asyncio
async def test_multi_parent_goal_via_tools(project_dir):
    results = await _run_session(project_dir, [
        ("create_tree", {"root_label": "Test", "project_root": project_dir}),
        ("add_goal", {"id": "a", "label": "A", "parents": ["root"]}),
        ("add_goal", {"id": "b", "label": "B", "parents": ["root"]}),
        ("add_goal", {"id": "c", "label": "C", "parents": ["a", "b"]}),
        ("list_priorities", {}),
    ])
    ranking = results[-1]
    # c should outrank a and b individually since it accumulates from both
    lines = [l for l in ranking.splitlines() if "(id:" in l]
    values = {}
    for line in lines:
        parts = line.strip().split()
        values[parts[-1].strip(")")] = float(parts[0])
    assert values["c"] > values["a"]
    assert values["c"] > values["b"]


@pytest.mark.asyncio
async def test_cannot_add_goal_before_create_tree(project_dir):
    results = await _run_session(project_dir, [
        ("add_goal", {"id": "a", "label": "A", "parents": ["root"]}),
    ])
    assert "Error" in results[0] or "load_tree" in results[0] or "create_tree" in results[0]


@pytest.mark.asyncio
async def test_link_artifacts_via_tool(project_dir):
    results = await _run_session(project_dir, [
        ("create_tree", {"root_label": "Test", "project_root": project_dir}),
        ("add_goal", {"id": "a", "label": "A", "parents": ["root"], "related_files": ["x.py"]}),
        ("link_artifacts", {"id": "a", "related_files": ["y.py"]}),
    ])
    assert "Linked artifacts" in results[-1]


@pytest.mark.asyncio
async def test_set_and_clear_active_goal(project_dir):
    results = await _run_session(project_dir, [
        ("create_tree", {"root_label": "Test", "project_root": project_dir}),
        ("add_goal", {"id": "a", "label": "A", "parents": ["root"]}),
        ("set_active_goal", {"ids": ["a"], "reason": "working on it"}),
        ("clear_active_goal", {}),
    ])
    assert "Marked active" in results[2]
    assert "Cleared" in results[3]


@pytest.mark.asyncio
async def test_set_active_goal_unknown_id_errors(project_dir):
    results = await _run_session(project_dir, [
        ("create_tree", {"root_label": "Test", "project_root": project_dir}),
        ("set_active_goal", {"ids": ["nonexistent"], "reason": "x"}),
    ])
    assert "Error" in results[-1]


@pytest.mark.asyncio
async def test_persistence_round_trip_across_sessions(project_dir):
    # Session 1: create and save
    await _run_session(project_dir, [
        ("create_tree", {"root_label": "Persisted", "project_root": project_dir}),
        ("add_goal", {"id": "a", "label": "Feature A", "parents": ["root"], "related_files": ["a.py"]}),
    ])

    save_path = os.path.join(project_dir, ".goalt", "tree.json")
    assert os.path.isfile(save_path)
    with open(save_path) as f:
        saved = json.load(f)
    assert len(saved["nodes"]) == 2

    # Session 2 (fresh subprocess): explicit load_tree should restore it
    results = await _run_session(project_dir, [
        ("load_tree", {"project_root": project_dir}),
    ])
    assert "Feature A" in results[0]


@pytest.mark.asyncio
async def test_load_tree_with_no_saved_tree_says_so(tmp_path):
    empty_dir = str(tmp_path / "nothing_here")
    os.makedirs(empty_dir)
    results = await _run_session(empty_dir, [
        ("load_tree", {"project_root": empty_dir}),
    ])
    assert "No saved tree found" in results[0]


@pytest.mark.asyncio
async def test_reset_tree_deletes_saved_file(project_dir):
    await _run_session(project_dir, [
        ("create_tree", {"root_label": "Test", "project_root": project_dir}),
    ])
    save_path = os.path.join(project_dir, ".goalt", "tree.json")
    assert os.path.isfile(save_path)

    await _run_session(project_dir, [
        ("load_tree", {"project_root": project_dir}),
        ("reset_tree", {}),
    ])
    assert not os.path.isfile(save_path)


@pytest.mark.asyncio
async def test_open_dashboard_returns_url(project_dir):
    results = await _run_session(project_dir, [
        ("open_dashboard", {}),
    ])
    assert "http://127.0.0.1:8765" in results[0]


@pytest.mark.asyncio
async def test_cycle_rejected_via_tools_returns_message_not_crash(project_dir):
    # add_goal can't structurally create a cycle (see goal_tree tests), but
    # confirm the tool layer at least handles a nonsense/duplicate-id
    # request gracefully rather than crashing the server.
    results = await _run_session(project_dir, [
        ("create_tree", {"root_label": "Test", "project_root": project_dir}),
        ("add_goal", {"id": "a", "label": "A", "parents": ["root"]}),
        ("add_goal", {"id": "a", "label": "A again", "parents": ["root"]}),  # duplicate id
    ])
    # whatever the exact message, it must not be a raw traceback / crash
    assert "Traceback" not in results[-1]
