"""GoalT -- a multi-parent, value-propagating goal graph.

Only the core engine is re-exported here. `visualize` (matplotlib),
`dashboard` (FastAPI) and `mcp_server` (mcp) each pull in optional
dependencies, so they stay importable by path rather than being loaded
eagerly on `import goaltree`:

    from goaltree.visualize import draw          # needs the [viz] extra
    from goaltree.dashboard import create_app    # needs the [mcp] extra
"""

from .goal_tree import (
    CycleError,
    Goal,
    GoalGraph,
    WeightError,
    equal_weight_redistribution,
    make_llm_redistribution_fn,
)

__all__ = [
    "CycleError",
    "Goal",
    "GoalGraph",
    "WeightError",
    "equal_weight_redistribution",
    "make_llm_redistribution_fn",
]

__version__ = "0.6.2"
