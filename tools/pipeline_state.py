"""
Pipeline state machine -- single source of truth for pipeline runs.

Every pipeline run gets:
- A UUID (run_id)
- A state.json file at .pineapple/runs/<run_id>/state.json
- Atomic transitions with append-only event log

State machine stages: INTAKE -> BRAINSTORM -> PLAN -> SETUP -> BUILD -> VERIFY -> REVIEW -> SHIP -> EVOLVE
Allowed transitions: forward by 1, REVIEW -> BUILD (retry loop), any -> FAILED
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------

class PipelineStage(str, Enum):
    INTAKE = "INTAKE"
    BRAINSTORM = "BRAINSTORM"
    PLAN = "PLAN"
    SETUP = "SETUP"
    BUILD = "BUILD"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    SHIP = "SHIP"
    EVOLVE = "EVOLVE"
    FAILED = "FAILED"


_STAGE_ORDER = [
    PipelineStage.INTAKE,
    PipelineStage.BRAINSTORM,
    PipelineStage.PLAN,
    PipelineStage.SETUP,
    PipelineStage.BUILD,
    PipelineStage.VERIFY,
    PipelineStage.REVIEW,
    PipelineStage.SHIP,
    PipelineStage.EVOLVE,
]

_STAGE_INDEX = {stage: idx for idx, stage in enumerate(_STAGE_ORDER)}

_DEFAULT_MAX_RETRIES = {
    "BUILD": 3,
    "VERIFY": 3,
    "REVIEW": 2,
}

_TERMINAL_STAGES = {PipelineStage.EVOLVE, PipelineStage.FAILED}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when a stage transition violates the state machine rules."""


class MaxRetriesExceeded(Exception):
    """Raised when a stage has been retried more than its max_retries limit."""


class PipelineTimeoutError(Exception):
    """Raised when a pipeline run exceeds its wall-clock timeout."""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class PipelineEvent(BaseModel):
    timestamp: str
    from_stage: PipelineStage
    to_stage: PipelineStage
    reason: str = ""
    metadata: dict = Field(default_factory=dict)


class PipelineRun(BaseModel):
    run_id: str
    feature_name: str
    branch: str
    current_stage: PipelineStage
    attempt_counts: dict[str, int] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    max_retries: dict[str, int] = Field(default_factory=lambda: dict(_DEFAULT_MAX_RETRIES))
    wall_clock_timeout_hours: float = 4.0
    events: list[PipelineEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# State manager
# ---------------------------------------------------------------------------

class PipelineState:
    """Manages pipeline run state on disk with atomic writes."""

    def __init__(self, project_path: Path) -> None:
        self.pineapple_dir = project_path / ".pineapple" / "runs"

    # -- public API ---------------------------------------------------------

    def create_run(self, feature_name: str, branch: str) -> PipelineRun:
        """Create a new pipeline run at INTAKE stage."""
        now = datetime.now(timezone.utc).isoformat()
        run_id = str(uuid.uuid4())
        run = PipelineRun(
            run_id=run_id,
            feature_name=feature_name,
            branch=branch,
            current_stage=PipelineStage.INTAKE,
            created_at=now,
            updated_at=now,
        )
        self._write_atomic(run_id, run)
        return run

    def advance(self, run_id: str, reason: str = "") -> PipelineRun:
        """Move to the next stage (forward by exactly 1 step).

        Raises InvalidTransitionError if the current stage is terminal.
        Raises PipelineTimeoutError if wall-clock timeout exceeded.
        """
        run = self.get_run(run_id)
        self._check_timeout(run)

        if run.current_stage in _TERMINAL_STAGES:
            raise InvalidTransitionError(
                f"Cannot advance from terminal stage {run.current_stage.value}"
            )

        current_idx = _STAGE_INDEX.get(run.current_stage)
        if current_idx is None or current_idx + 1 >= len(_STAGE_ORDER):
            raise InvalidTransitionError(
                f"Cannot advance from {run.current_stage.value}"
            )

        next_stage = _STAGE_ORDER[current_idx + 1]
        event = PipelineEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            from_stage=run.current_stage,
            to_stage=next_stage,
            reason=reason,
        )

        run.current_stage = next_stage
        run.updated_at = event.timestamp
        run.events.append(event)
        self._write_atomic(run_id, run)
        return run

    def retry(self, run_id: str, reason: str = "") -> PipelineRun:
        """Retry from REVIEW back to BUILD.

        Only valid when current_stage is REVIEW. Increments the BUILD
        attempt count and checks against max_retries.

        Raises InvalidTransitionError if not in REVIEW.
        Raises MaxRetriesExceeded if BUILD retries exhausted.
        Raises PipelineTimeoutError if wall-clock timeout exceeded.
        """
        run = self.get_run(run_id)
        self._check_timeout(run)

        if run.current_stage != PipelineStage.REVIEW:
            raise InvalidTransitionError(
                f"retry() is only valid from REVIEW, current stage is {run.current_stage.value}"
            )

        build_key = PipelineStage.BUILD.value
        current_attempts = run.attempt_counts.get(build_key, 0)
        max_allowed = run.max_retries.get(build_key, _DEFAULT_MAX_RETRIES[build_key])

        if current_attempts >= max_allowed:
            raise MaxRetriesExceeded(
                f"BUILD has been retried {current_attempts} times "
                f"(max {max_allowed}). Run {run_id} cannot retry again."
            )

        run.attempt_counts[build_key] = current_attempts + 1

        event = PipelineEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            from_stage=PipelineStage.REVIEW,
            to_stage=PipelineStage.BUILD,
            reason=reason,
            metadata={"attempt": current_attempts + 1},
        )

        run.current_stage = PipelineStage.BUILD
        run.updated_at = event.timestamp
        run.events.append(event)
        self._write_atomic(run_id, run)
        return run

    def fail(self, run_id: str, reason: str = "") -> PipelineRun:
        """Move any stage to FAILED."""
        run = self.get_run(run_id)

        event = PipelineEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            from_stage=run.current_stage,
            to_stage=PipelineStage.FAILED,
            reason=reason,
        )

        run.current_stage = PipelineStage.FAILED
        run.updated_at = event.timestamp
        run.events.append(event)
        self._write_atomic(run_id, run)
        return run

    def get_run(self, run_id: str) -> PipelineRun:
        """Read and return a pipeline run from disk."""
        state_file = self._state_file(run_id)
        if not state_file.exists():
            raise FileNotFoundError(f"No pipeline run found at {state_file}")
        text = state_file.read_text(encoding="utf-8")
        return PipelineRun.model_validate_json(text)

    def list_active_runs(self) -> list[PipelineRun]:
        """List all runs not in a terminal state (EVOLVE or FAILED)."""
        active: list[PipelineRun] = []
        if not self.pineapple_dir.exists():
            return active

        for run_dir in self.pineapple_dir.iterdir():
            if not run_dir.is_dir():
                continue
            state_file = run_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                run = PipelineRun.model_validate_json(
                    state_file.read_text(encoding="utf-8")
                )
                if run.current_stage not in _TERMINAL_STAGES:
                    active.append(run)
            except Exception:
                # Skip corrupt state files
                continue

        return active

    # -- internal helpers ---------------------------------------------------

    def _check_timeout(self, run: PipelineRun) -> None:
        """Raise PipelineTimeoutError if wall-clock timeout exceeded."""
        created = datetime.fromisoformat(run.created_at)
        elapsed_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if elapsed_hours > run.wall_clock_timeout_hours:
            raise PipelineTimeoutError(
                f"Run {run.run_id} exceeded {run.wall_clock_timeout_hours}h wall-clock timeout "
                f"({elapsed_hours:.1f}h elapsed)"
            )

    def _write_atomic(self, run_id: str, run: PipelineRun) -> None:
        """Write state to a temp file, then atomically replace."""
        state_file = self._state_file(run_id)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        os.replace(str(tmp), str(state_file))

    def _run_dir(self, run_id: str) -> Path:
        """Return the directory for a given run."""
        return self.pineapple_dir / run_id

    def _state_file(self, run_id: str) -> Path:
        """Return the state.json path for a given run."""
        return self._run_dir(run_id) / "state.json"
