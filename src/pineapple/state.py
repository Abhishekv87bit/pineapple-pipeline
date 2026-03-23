"""Pipeline state schema for LangGraph."""
from __future__ import annotations

import enum
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


class PipelineStage(str, enum.Enum):
    """The ten stages of the Pineapple Pipeline."""

    INTAKE = "intake"
    STRATEGIC_REVIEW = "strategic_review"
    ARCHITECTURE = "architecture"
    PLAN = "plan"
    SETUP = "setup"
    BUILD = "build"
    VERIFY = "verify"
    REVIEW = "review"
    SHIP = "ship"
    EVOLVE = "evolve"


class PipelineState(TypedDict):
    """LangGraph state for a pipeline run."""

    # Identity
    run_id: str
    request: str
    project_name: str
    branch: str
    path: Literal["full", "medium", "lightweight"]
    current_stage: str  # PipelineStage value

    # Stage artifacts (populated by each stage's node)
    context_bundle: dict | None
    strategic_brief: dict | None
    design_spec: dict | None
    task_plan: dict | None
    workspace_info: dict | None
    build_results: list[dict]
    verify_record: dict | None
    review_result: dict | None
    ship_result: dict | None
    evolve_report: dict | None

    # Control flow
    attempt_counts: dict[str, int]
    human_approvals: dict[str, bool]
    cost_total_usd: float
    errors: list[dict]
    messages: Annotated[list[BaseMessage], add_messages]
