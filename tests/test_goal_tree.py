"""Tests for goaltree.goal_tree -- the core graph engine.

Run with: pytest tests/test_goal_tree.py -v
"""

import json

import pytest

from goaltree.goal_tree import (
    GoalGraph,
    CycleError,
    WeightError,
    equal_weight_redistribution,
    make_llm_redistribution_fn,
)


# ---------- basic construction ----------

def test_add_root_sets_value_to_one():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    assert g.g.nodes["root"]["goal"].value == 1.0
    assert g.root_id == "root"


def test_cannot_add_second_root():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    with pytest.raises(ValueError, match="Root already set"):
        g.add_root("root2", "Another root")


def test_add_goal_before_root_raises():
    g = GoalGraph()
    with pytest.raises(ValueError, match="add_root"):
        g.add_goal("a", "A", parents=["root"])


def test_add_goal_requires_at_least_one_parent():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    with pytest.raises(ValueError, match="at least one parent"):
        g.add_goal("a", "A", parents=[])


def test_add_goal_unknown_parent_raises_and_rolls_back():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    with pytest.raises(ValueError, match="Unknown parent"):
        g.add_goal("a", "A", parents=["nonexistent"])
    assert "a" not in g.g.nodes  # rolled back, not left half-added


# ---------- value propagation ----------

def test_equal_split_among_two_children():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    assert g.g.nodes["a"]["goal"].value == pytest.approx(0.5)
    assert g.g.nodes["b"]["goal"].value == pytest.approx(0.5)


def test_multi_parent_accumulates_value_from_both():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    g.add_goal("c", "C", parents=["a", "b"])  # only child of both a and b
    # c gets ALL of a's value (0.5) plus ALL of b's value (0.5) = 1.0
    assert g.g.nodes["c"]["goal"].value == pytest.approx(1.0)


def test_incremental_add_only_reweights_affected_siblings():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    g.add_goal("c", "C", parents=["a", "b"])
    g.add_goal("d", "D", parents=["a"])  # a now splits its 0.5 between c and d
    assert g.g.nodes["a"]["goal"].value == pytest.approx(0.5)  # a itself unaffected
    assert g.g.nodes["b"]["goal"].value == pytest.approx(0.5)  # b unaffected
    assert g.g.nodes["c"]["goal"].value == pytest.approx(0.75)  # 0.25 from a + 0.5 from b
    assert g.g.nodes["d"]["goal"].value == pytest.approx(0.25)


def test_children_weights_sum_to_one_per_parent():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    g.add_goal("c", "C", parents=["root"])
    check = g.sum_check()
    assert check["root"] == pytest.approx(1.0)


# ---------- cycle rejection ----------

def test_cycle_error_class_exists_for_future_linking_feature():
    """CycleError isn't reachable through add_goal alone (a new node has no
    outgoing edges yet, so it can't complete a cycle) -- it's a defensive
    backstop for a planned future feature (linking two existing goals).
    This just documents that the class exists and is importable."""
    assert issubclass(CycleError, Exception)


def test_add_goal_via_public_api_cannot_create_cycle_by_construction():
    """A new node has no outgoing edges yet, so add_goal alone can never
    produce a cycle -- this documents that structural guarantee."""
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["a"])
    # adding a new goal under b is fine and cannot cycle back to root/a
    g.add_goal("c", "C", parents=["b", "root"])
    assert "c" in g.g.nodes


# ---------- weight validation / redistribution ----------

def test_missing_weight_for_a_child_raises_weight_error():
    def broken_fn(parent_id, children_ids, context):
        return {children_ids[0]: 1.0}  # missing weight for second child

    g = GoalGraph(redistribution_fn=broken_fn)
    g.add_root("root", "Ship v2")
    with pytest.raises(WeightError, match="did not return a weight"):
        g.add_goal("a", "A", parents=["root"])
        g.add_goal("b", "B", parents=["root"])


def test_negative_weight_raises_weight_error():
    def broken_fn(parent_id, children_ids, context):
        return {c: -1.0 for c in children_ids}

    g = GoalGraph(redistribution_fn=broken_fn)
    g.add_root("root", "Ship v2")
    with pytest.raises(WeightError, match="non-negative"):
        g.add_goal("a", "A", parents=["root"])


def test_all_zero_weights_falls_back_to_equal_split():
    def zero_fn(parent_id, children_ids, context):
        return {c: 0.0 for c in children_ids}

    g = GoalGraph(redistribution_fn=zero_fn)
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    assert g.g.nodes["a"]["goal"].value == pytest.approx(0.5)
    assert g.g.nodes["b"]["goal"].value == pytest.approx(0.5)


def test_llm_redistribution_fn_uses_weights_from_model():
    def fake_llm_call(prompt: str) -> str:
        return json.dumps({"a": 3, "b": 1})

    llm_fn = make_llm_redistribution_fn(fake_llm_call)
    g = GoalGraph(redistribution_fn=llm_fn)
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    assert g.g.nodes["a"]["goal"].value == pytest.approx(0.75)
    assert g.g.nodes["b"]["goal"].value == pytest.approx(0.25)


def test_llm_redistribution_fn_falls_back_on_garbage_response():
    def broken_llm_call(prompt: str) -> str:
        return "not valid json at all"

    llm_fn = make_llm_redistribution_fn(broken_llm_call)
    g = GoalGraph(redistribution_fn=llm_fn)
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    # should silently fall back to equal split rather than crash
    assert g.g.nodes["a"]["goal"].value == pytest.approx(0.5)
    assert g.g.nodes["b"]["goal"].value == pytest.approx(0.5)


# ---------- descriptions and artifacts ----------

def test_description_and_artifacts_stored_on_creation():
    g = GoalGraph()
    g.add_root("root", "Ship v2", description="top level")
    g.add_goal(
        "a", "A", parents=["root"], description="does A",
        related_files=["src/a.py"], related_backend=["orders table"],
    )
    goal = g.g.nodes["a"]["goal"]
    assert goal.description == "does A"
    assert goal.related_files == ["src/a.py"]
    assert goal.related_backend == ["orders table"]


def test_link_artifacts_appends_and_dedupes():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["x.py"])
    g.link_artifacts("a", files=["y.py", "x.py"])  # x.py is a duplicate
    assert g.g.nodes["a"]["goal"].related_files == ["x.py", "y.py"]


def test_link_artifacts_replace_overwrites():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["x.py"])
    g.link_artifacts("a", files=["z.py"], replace=True)
    assert g.g.nodes["a"]["goal"].related_files == ["z.py"]


def test_link_artifacts_unknown_id_raises():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    with pytest.raises(ValueError, match="Unknown goal id"):
        g.link_artifacts("nonexistent", files=["x.py"])


# ---------- goals_for_file matching ----------

def test_goals_for_file_exact_match():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["src/a.py"])
    assert g.goals_for_file("src/a.py") == ["a"]


def test_goals_for_file_suffix_match_with_absolute_path():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["src/a.py"])
    assert g.goals_for_file("/Users/someone/project/src/a.py") == ["a"]


def test_goals_for_file_no_match_returns_empty():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["src/a.py"])
    assert g.goals_for_file("src/unrelated.py") == []


def test_goals_for_file_matches_multiple_goals():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"], related_files=["shared.py"])
    g.add_goal("b", "B", parents=["root"], related_files=["shared.py"])
    assert set(g.goals_for_file("shared.py")) == {"a", "b"}


# ---------- serialization round-trip ----------

def test_to_dict_from_dict_round_trip_is_exact():
    g = GoalGraph()
    g.add_root("root", "Ship v2", description="top")
    g.add_goal("a", "A", parents=["root"], related_files=["x.py"])
    g.add_goal("b", "B", parents=["root"], related_backend=["table"])
    g.add_goal("c", "C", parents=["a", "b"])

    original = g.to_dict()
    restored = GoalGraph.from_dict(original)
    assert restored.to_dict() == original


def test_from_dict_restored_graph_supports_further_edits():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    restored = GoalGraph.from_dict(g.to_dict())
    restored.add_goal("b", "B", parents=["root"])
    assert restored.g.nodes["a"]["goal"].value == pytest.approx(0.5)
    assert restored.g.nodes["b"]["goal"].value == pytest.approx(0.5)


# ---------- ranked() / repr ----------

def test_ranked_sorts_descending_by_value():
    g = GoalGraph()
    g.add_root("root", "Ship v2")
    g.add_goal("a", "A", parents=["root"])
    g.add_goal("b", "B", parents=["root"])
    g.add_goal("c", "C", parents=["a", "b"])  # highest value after root
    ranked_ids = [r[0] for r in g.ranked()]
    assert ranked_ids[0] == "root"
    assert ranked_ids[1] == "c"


def test_equal_weight_redistribution_helper_directly():
    weights = equal_weight_redistribution("p", ["a", "b", "c"], {})
    assert weights == {"a": pytest.approx(1 / 3), "b": pytest.approx(1 / 3), "c": pytest.approx(1 / 3)}


def test_equal_weight_redistribution_no_children():
    assert equal_weight_redistribution("p", [], {}) == {}
