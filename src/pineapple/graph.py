"""LangGraph graph definition for the Pineapple Pipeline v2.

Ten-stage agentic development pipeline with three path options
(full, medium, lightweight) and a review loop with circuit breaker.
"""
import os
import sqlite3

try:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, StateGraph

    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False
    MemorySaver = None  # type: ignore[assignment,misc]
    SqliteSaver = None  # type: ignore[assignment,misc]
    StateGraph = None  # type: ignore[assignment,misc]
    END = None  # type: ignore[assignment,misc]

from pineapple.agents.architecture import architecture_node
from pineapple.agents.builder import builder_node
from pineapple.agents.evolver import evolve_node
from pineapple.agents.intake import intake_node
from pineapple.agents.planner import plan_node
from pineapple.agents.reviewer import reviewer_node
from pineapple.agents.setup import setup_node
from pineapple.agents.shipper import ship_node
from pineapple.agents.strategic_review import strategic_review_node
from pineapple.agents.verifier import verifier_node
from pineapple.gates import review_gate, route_by_path
from pineapple.state import PipelineState

# Nodes that require human approval before executing.
INTERRUPT_NODES: list[str] = ["strategic_review", "architecture", "plan", "ship"]


# ---------------------------------------------------------------------------
# Circuit breaker node
# ---------------------------------------------------------------------------


def human_intervention_node(state: PipelineState) -> dict:
    """Circuit breaker: pipeline halted, requires human intervention."""
    project_name = state.get("project_name", "unknown")
    review_result = state.get("review_result")
    errors = state.get("errors", [])

    print(f"[Human Intervention] Pipeline halted for: {project_name}")
    print(f"  Review verdict: {review_result.get('verdict', 'unknown') if review_result else 'N/A'}")
    if review_result and review_result.get("critical_issues"):
        for issue in review_result["critical_issues"]:
            print(f"  CRITICAL: {issue}")
    print(f"  Total errors: {len(errors)}")
    print("  Action required: human must inspect and decide next steps.")

    return {"current_stage": "human_intervention"}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def _make_sqlite_checkpointer(db_path: str):
    """Create a SqliteSaver checkpointer, ensuring the parent directory exists.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A ready-to-use SqliteSaver instance.
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


_SENTINEL = object()


def create_pipeline(checkpointer=_SENTINEL, db_path=".pineapple/checkpoints.db"):
    """Build and compile the Pineapple Pipeline graph.

    Args:
        checkpointer: LangGraph checkpointer instance. If provided, used
                      directly (pass None to force in-memory MemorySaver).
        db_path: Path to SQLite database for persistent checkpoints.
                 Defaults to '.pineapple/checkpoints.db'. Only used when
                 checkpointer is not explicitly provided. Set to None to
                 fall back to MemorySaver.

    Returns:
        A compiled StateGraph ready to invoke.

    Raises:
        RuntimeError: If langgraph is not installed.
    """
    if not _HAS_LANGGRAPH:
        raise RuntimeError(
            "langgraph is required. Install with: pip install 'pineapple-pipeline[llm]'"
        )

    if checkpointer is not _SENTINEL:
        # Caller explicitly provided a checkpointer (or None for MemorySaver)
        if checkpointer is None:
            checkpointer = MemorySaver()
    elif db_path is not None:
        checkpointer = _make_sqlite_checkpointer(db_path)
    else:
        checkpointer = MemorySaver()

    graph = StateGraph(PipelineState)

    # -- Add all nodes -------------------------------------------------------
    graph.add_node("intake", intake_node)
    graph.add_node("strategic_review", strategic_review_node)
    graph.add_node("architecture", architecture_node)
    graph.add_node("plan", plan_node)
    graph.add_node("setup", setup_node)
    graph.add_node("build", builder_node)
    graph.add_node("verify", verifier_node)
    graph.add_node("review", reviewer_node)
    graph.add_node("ship", ship_node)
    graph.add_node("evolve", evolve_node)
    graph.add_node("human_intervention", human_intervention_node)

    # -- Entry point ---------------------------------------------------------
    graph.set_entry_point("intake")

    # -- Edges ---------------------------------------------------------------

    # After intake: route based on path selection
    graph.add_conditional_edges(
        "intake",
        route_by_path,
        {
            "strategic_review": "strategic_review",
            "plan": "plan",
            "build": "build",
        },
    )

    # Full path linear chain: strategic_review -> architecture -> plan
    graph.add_edge("strategic_review", "architecture")
    graph.add_edge("architecture", "plan")

    # All paths converge: plan -> setup -> build -> verify -> review
    graph.add_edge("plan", "setup")
    graph.add_edge("setup", "build")
    graph.add_edge("build", "verify")
    graph.add_edge("verify", "review")

    # After review: conditional routing via review_gate
    graph.add_conditional_edges(
        "review",
        review_gate,
        {
            "pass": "ship",
            "retry": "build",
            "fail": "human_intervention",
        },
    )

    # ship -> evolve -> END
    graph.add_edge("ship", "evolve")
    graph.add_edge("evolve", END)

    # human_intervention -> END
    graph.add_edge("human_intervention", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=INTERRUPT_NODES,
    )
