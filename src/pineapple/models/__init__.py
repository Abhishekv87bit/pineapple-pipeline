"""Pydantic models for inter-stage artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from typing import Any as _Any

from pydantic import BaseModel, Field


# --- Intake ---


class ContextBundle(BaseModel):
    """Output of the Intake stage: gathered project context."""

    project_type: str
    context_files: list[str] = Field(default_factory=list)
    classification: str
    codebase_summary: dict = Field(default_factory=dict)
    project_memory: dict = Field(default_factory=dict)
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    similar_projects: list[dict] = Field(default_factory=list)


# --- Strategic Review ---


class StrategicBrief(BaseModel):
    """Output of Strategic Review: scoped intent and assumptions."""

    what: str
    why: str
    not_building: list[str] = Field(default_factory=list)
    who_benefits: str
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    approved: bool = False


# --- Architecture ---


class ComponentSpec(BaseModel):
    """A single component within a design specification."""

    name: str
    description: str
    files: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)


class TechnologyChoice(BaseModel):
    """A single technology choice within a design specification.

    Gemini structured output cannot reliably populate free-form dict[str, str]
    fields. Using an explicit list of (category, choice) pairs avoids this.
    """

    category: str = Field(description="Technology category, e.g. 'language', 'framework', 'database'")
    choice: str = Field(description="The chosen technology, e.g. 'Python 3.12', 'FastAPI', 'PostgreSQL'")


class DesignSpec(BaseModel):
    """Output of Architecture: technical design with components."""

    title: str
    summary: str
    components: list[ComponentSpec] = Field(default_factory=list)
    technology_choices_list: list[TechnologyChoice] = Field(
        default_factory=list,
        description="Technology choices as structured list (preferred for LLM extraction)",
    )
    approved: bool = False

    @property
    def technology_choices(self) -> dict[str, str]:
        """Return technology choices as a dict for backward compatibility."""
        return {tc.category: tc.choice for tc in self.technology_choices_list}

    def model_dump(self, **kwargs: _Any) -> dict[str, _Any]:
        """Override to include technology_choices dict for backward compatibility.

        Downstream consumers (planner, dogfood scripts, JSON artifacts) all
        expect a ``technology_choices`` dict key in the serialized output.
        """
        data = super().model_dump(**kwargs)
        data["technology_choices"] = {tc.category: tc.choice for tc in self.technology_choices_list}
        return data


# --- Plan ---


class Task(BaseModel):
    """A single implementation task."""

    id: str
    description: str
    files_to_create: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)
    complexity: Literal["trivial", "standard", "complex"] = "standard"
    estimated_cost_usd: float = 0.0
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"


class TaskPlan(BaseModel):
    """Output of Plan: ordered task list with cost estimates."""

    tasks: list[Task] = Field(default_factory=list)
    total_estimated_cost_usd: float = 0.0
    approved: bool = False


# --- Build ---


class FileWrite(BaseModel):
    """A file to be written to disk during Build."""

    path: str = Field(description="Relative file path from project root (e.g., 'src/module.py')")
    content: str = Field(description="Complete source code for this file. Must be full, runnable implementation — not a stub, not a description, not a single import line.")


class BuildResult(BaseModel):
    """Result of executing a single task during Build."""

    task_id: str
    status: Literal["completed", "failed"]
    commits: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    files_written: list[FileWrite] = Field(default_factory=list)


# --- Verify ---


class LayerResult(BaseModel):
    """Result of a single verification layer."""

    layer: int
    name: str
    status: Literal["pass", "fail", "skip"]
    details: str = ""
    test_count: int = 0
    fail_count: int = 0


class VerificationRecord(BaseModel):
    """Output of Verify: multi-layer test results."""

    all_green: bool
    layers: list[LayerResult] = Field(default_factory=list)
    integrity_hash: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- Review ---


class ReviewResult(BaseModel):
    """Output of Review: code review verdict."""

    verdict: Literal["pass", "retry", "fail"]
    critical_issues: list[str] = Field(default_factory=list)
    important_issues: list[str] = Field(default_factory=list)
    minor_issues: list[str] = Field(default_factory=list)


# --- Ship ---


class ShipResult(BaseModel):
    """Output of Ship: merge/PR action taken."""

    action: Literal["merge", "pr", "keep", "discard"]
    pr_url: str | None = None
    merge_commit: str | None = None


# --- Evolve ---


class EvolveReport(BaseModel):
    """Output of Evolve: session wrap-up and knowledge capture."""

    session_handoff_path: str
    bible_updated: bool = False
    decisions_logged: list[str] = Field(default_factory=list)
    memory_extractions: list[str] = Field(default_factory=list)


# --- Shared ---


class PipelineError(BaseModel):
    """An error recorded during pipeline execution."""

    stage: str
    message: str
    timestamp: str
    recoverable: bool = True


__all__ = [
    "ContextBundle",
    "StrategicBrief",
    "ComponentSpec",
    "TechnologyChoice",
    "DesignSpec",
    "Task",
    "TaskPlan",
    "FileWrite",
    "BuildResult",
    "LayerResult",
    "VerificationRecord",
    "ReviewResult",
    "ShipResult",
    "EvolveReport",
    "PipelineError",
]
