"""Tests for goaltree.dashboard -- pure helper functions, git polling logic, and
the FastAPI endpoints (via TestClient, no real server needed).

Run with: pytest tests/test_dashboard.py -v
"""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from goaltree.goal_tree import GoalGraph
from goaltree.dashboard import (
    _describe_tool_call,
    _prune_stale_file_edits,
    _git_uncommitted_files,
    _launch_diff_editor,
    create_app,
    FILE_EDIT_ACTIVE_WINDOW,
)


# ---------- _describe_tool_call ----------

def test_describe_tool_call_edit():
    desc = _describe_tool_call("Edit", {"file_path": "src/a.py"})
    assert desc == "Editing src/a.py"


def test_describe_tool_call_bash_truncates_long_commands():
    long_cmd = "x" * 100
    desc = _describe_tool_call("Bash", {"command": long_cmd})
    assert desc.startswith("Running: ")
    assert desc.endswith("...")
    assert len(desc) < len(long_cmd)


def test_describe_tool_call_bash_short_command_no_truncation():
    desc = _describe_tool_call("Bash", {"command": "ls -la"})
    assert desc == "Running: ls -la"


def test_describe_tool_call_unknown_tool_falls_back():
    desc = _describe_tool_call("ToolSearch", {})
    assert desc == "Running: ToolSearch"


# ---------- _prune_stale_file_edits ----------

def test_prune_stale_file_edits_removes_old_entries():
    state = {
        "file_edit_goals_raw": {
            "fresh": {"file": "a.py", "timestamp": time.time()},
            "stale": {"file": "b.py", "timestamp": time.time() - FILE_EDIT_ACTIVE_WINDOW - 10},
        }
    }
    _prune_stale_file_edits(state)
    assert "fresh" in state["file_edit_goals_raw"]
    assert "stale" not in state["file_edit_goals_raw"]


def test_prune_stale_file_edits_handles_empty_state():
    state = {}
    _prune_stale_file_edits(state)  # should not raise
    assert state["file_edit_goals_raw"] == {}


# ---------- _git_uncommitted_files ----------

@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=repo, check=True,
    )
    return repo


def test_git_uncommitted_files_empty_when_clean(git_repo):
    assert _git_uncommitted_files(str(git_repo)) == []


def test_git_uncommitted_files_detects_modified_file(git_repo):
    (git_repo / "tracked.py").write_text("original\nmodified\n")
    files = _git_uncommitted_files(str(git_repo))
    assert "tracked.py" in files


def test_git_uncommitted_files_detects_untracked_file(git_repo):
    (git_repo / "new_file.py").write_text("new\n")
    files = _git_uncommitted_files(str(git_repo))
    assert "new_file.py" in files


def test_git_uncommitted_files_returns_empty_for_non_git_dir(tmp_path):
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    assert _git_uncommitted_files(str(not_a_repo)) == []


# ---------- _launch_diff_editor (mocked subprocess, no real GUI needed) ----------

class _FakeResult:
    def __init__(self, returncode):
        self.returncode = returncode


def _make_fake_runner(behaviors):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        behavior = behaviors[len(calls) - 1]
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeResult(behavior)

    runner.calls = calls
    return runner


def test_launch_diff_editor_macos_first_app_succeeds():
    runner = _make_fake_runner([0])
    ok, err = _launch_diff_editor("/tmp/a", "/tmp/b", runner=runner, is_macos=True)
    assert ok is True and err is None
    assert runner.calls[0][:3] == ["open", "-a", "Visual Studio Code"]
    assert len(runner.calls) == 1


def test_launch_diff_editor_macos_falls_back_to_code_cli():
    runner = _make_fake_runner([1, 1, 1, 0])  # 3 open -a variants fail, code CLI succeeds
    ok, err = _launch_diff_editor("/tmp/a", "/tmp/b", runner=runner, is_macos=True)
    assert ok is True
    assert runner.calls[3][0] == "code"


def test_launch_diff_editor_total_failure_gives_actionable_message():
    runner = _make_fake_runner([1, 1, 1, FileNotFoundError()])
    ok, err = _launch_diff_editor("/tmp/a", "/tmp/b", runner=runner, is_macos=True)
    assert ok is False
    assert "Shell Command" in err


def test_launch_diff_editor_non_macos_skips_open_a():
    runner = _make_fake_runner([0])
    ok, err = _launch_diff_editor("/tmp/a", "/tmp/b", runner=runner, is_macos=False)
    assert ok is True
    assert runner.calls[0][0] == "code"
    assert len(runner.calls) == 1


# ---------- FastAPI endpoints via TestClient ----------

@pytest.fixture
def app_state():
    return {"graph": None, "active_goals": {}, "last_activity": None, "project_root": None,
             "file_edit_goals_raw": {}, "uncommitted": {}}


@pytest.fixture
def client(app_state):
    return TestClient(create_app(app_state))


def test_api_graph_empty_when_no_tree(client):
    resp = client.get("/api/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["project_root"] is None


def test_api_graph_reflects_real_tree(client, app_state):
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["x.py"])
    app_state["graph"] = g
    app_state["project_root"] = "/tmp/proj"

    resp = client.get("/api/graph")
    data = resp.json()
    assert len(data["nodes"]) == 2
    assert data["project_root"] == "/tmp/proj"
    a_node = next(n for n in data["nodes"] if n["id"] == "a")
    assert a_node["related_files"] == ["x.py"]


def test_hook_pre_tool_use_updates_last_activity(client, app_state):
    resp = client.post("/hooks/pre-tool-use", json={
        "tool_name": "Bash", "tool_input": {"command": "npm test"}
    })
    assert resp.status_code == 200
    assert resp.json()["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert app_state["last_activity"]["tool_name"] == "Bash"


def test_hook_pre_tool_use_auto_highlights_matching_goal(client, app_state):
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["src/a.py"])
    app_state["graph"] = g

    client.post("/hooks/pre-tool-use", json={
        "tool_name": "Edit", "tool_input": {"file_path": "src/a.py"}
    })
    assert "a" in app_state["file_edit_goals_raw"]


def test_hook_pre_tool_use_does_not_highlight_unrelated_goal(client, app_state):
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["src/a.py"])
    g.add_goal("b", "B", parents=["root"], related_files=["src/b.py"])
    app_state["graph"] = g

    client.post("/hooks/pre-tool-use", json={
        "tool_name": "Edit", "tool_input": {"file_path": "src/a.py"}
    })
    assert "a" in app_state["file_edit_goals_raw"]
    assert "b" not in app_state["file_edit_goals_raw"]


def test_open_diff_missing_params_returns_400(client):
    resp = client.post("/api/open-diff", json={})
    assert resp.status_code == 400


def test_open_diff_file_not_found_returns_404(client, app_state):
    app_state["project_root"] = "/tmp"
    resp = client.post("/api/open-diff", json={"file": "does_not_exist_xyz.py"})
    assert resp.status_code == 404


def test_open_diff_untracked_file_returns_404(client, app_state, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "untracked.py").write_text("x")
    app_state["project_root"] = str(tmp_path)
    resp = client.post("/api/open-diff", json={"file": "untracked.py"})
    assert resp.status_code == 404
    assert "No committed version" in resp.json()["error"]
