"""Tests for pipeline_state.py — Pineapple Pipeline Tier 2 state machine.

The module under test does not exist yet; these tests are written from spec.
All tests use tmp_path fixtures and have no real filesystem dependencies.
"""

import pytest
import time
from datetime import datetime

from pipeline_state import (
    PipelineStage,
    PipelineState,
    PipelineRun,
    InvalidTransitionError,
    MaxRetriesExceeded,
    PipelineTimeoutError,
)


class TestCreateRun:
    def test_create_run_returns_valid_uuid(self, tmp_path):
        """Run ID should be a valid UUID4 string."""
        state = PipelineState(tmp_path)
        run = state.create_run("test-feature", "feat/test")
        assert len(run.run_id) == 36  # UUID format
        assert run.feature_name == "test-feature"
        assert run.branch == "feat/test"
        assert run.current_stage == PipelineStage.INTAKE

    def test_create_run_writes_state_file(self, tmp_path):
        """State file should exist on disk after create."""
        state = PipelineState(tmp_path)
        run = state.create_run("test-feature", "feat/test")
        state_file = tmp_path / ".pineapple" / "runs" / run.run_id / "state.json"
        assert state_file.is_file()

    def test_create_run_initial_stage_is_intake(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        assert run.current_stage == PipelineStage.INTAKE

    def test_create_run_has_timestamps(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        assert run.created_at is not None
        assert run.updated_at is not None
        # Should be valid ISO format
        datetime.fromisoformat(run.created_at)
        datetime.fromisoformat(run.updated_at)


class TestAdvanceStage:
    def test_advance_intake_to_brainstorm(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        advanced = state.advance(run.run_id, "Starting brainstorm")
        assert advanced.current_stage == PipelineStage.BRAINSTORM

    def test_advance_full_happy_path(self, tmp_path):
        """Should be able to advance through all 9 stages."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        expected_stages = [
            PipelineStage.BRAINSTORM,
            PipelineStage.PLAN,
            PipelineStage.SETUP,
            PipelineStage.BUILD,
            PipelineStage.VERIFY,
            PipelineStage.REVIEW,
            PipelineStage.SHIP,
            PipelineStage.EVOLVE,
        ]
        for expected in expected_stages:
            run = state.advance(run.run_id, f"Moving to {expected.value}")
            assert run.current_stage == expected

    def test_advance_from_evolve_raises(self, tmp_path):
        """Cannot advance from terminal EVOLVE stage."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        # Walk to EVOLVE
        for _ in range(8):
            run = state.advance(run.run_id)
        assert run.current_stage == PipelineStage.EVOLVE
        with pytest.raises(InvalidTransitionError):
            state.advance(run.run_id)

    def test_advance_from_failed_raises(self, tmp_path):
        """Cannot advance from terminal FAILED stage."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        state.fail(run.run_id, "test failure")
        with pytest.raises(InvalidTransitionError):
            state.advance(run.run_id)

    def test_advance_records_event(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        run = state.advance(run.run_id, "test reason")
        assert len(run.events) == 1
        assert run.events[0].from_stage == PipelineStage.INTAKE
        assert run.events[0].to_stage == PipelineStage.BRAINSTORM
        assert run.events[0].reason == "test reason"

    def test_advance_updates_timestamp(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        old_updated = run.updated_at
        time.sleep(0.01)
        run = state.advance(run.run_id)
        assert run.updated_at >= old_updated


class TestRetry:
    def test_retry_review_to_build(self, tmp_path):
        """retry() should move from REVIEW back to BUILD."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        # Advance to REVIEW (6 steps: INTAKE->BRAIN->PLAN->SETUP->BUILD->VERIFY->REVIEW)
        for _ in range(6):
            run = state.advance(run.run_id)
        assert run.current_stage == PipelineStage.REVIEW
        run = state.retry(run.run_id, "Critical issues found")
        assert run.current_stage == PipelineStage.BUILD

    def test_retry_increments_attempt_count(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        for _ in range(6):
            run = state.advance(run.run_id)
        run = state.retry(run.run_id)
        assert run.attempt_counts.get("BUILD", 0) >= 1

    def test_retry_exceeds_max_raises(self, tmp_path):
        """Should raise MaxRetriesExceeded when retry limit hit."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        # Advance to REVIEW
        for _ in range(6):
            run = state.advance(run.run_id)
        # Retry loop until max exceeded (default BUILD max_retries = 3)
        for i in range(3):
            run = state.retry(run.run_id, f"retry {i}")
            # Advance back to REVIEW: BUILD->VERIFY->REVIEW = 2 steps
            run = state.advance(run.run_id)  # BUILD -> VERIFY
            run = state.advance(run.run_id)  # VERIFY -> REVIEW
        # One more retry should fail
        with pytest.raises(MaxRetriesExceeded):
            state.retry(run.run_id)

    def test_retry_from_non_review_raises(self, tmp_path):
        """retry() only valid from REVIEW stage."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        run = state.advance(run.run_id)  # BRAINSTORM
        with pytest.raises(InvalidTransitionError):
            state.retry(run.run_id)


class TestFail:
    def test_fail_from_any_stage(self, tmp_path):
        """fail() should work from any non-terminal stage."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        run = state.advance(run.run_id)  # BRAINSTORM
        run = state.fail(run.run_id, "something went wrong")
        assert run.current_stage == PipelineStage.FAILED

    def test_fail_records_event(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        run = state.fail(run.run_id, "oops")
        assert any(e.to_stage == PipelineStage.FAILED for e in run.events)


class TestGetRun:
    def test_get_run_returns_persisted_state(self, tmp_path):
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        state.advance(run.run_id)
        loaded = state.get_run(run.run_id)
        assert loaded.current_stage == PipelineStage.BRAINSTORM

    def test_get_run_nonexistent_raises(self, tmp_path):
        state = PipelineState(tmp_path)
        with pytest.raises(FileNotFoundError):
            state.get_run("nonexistent-uuid")


class TestListActiveRuns:
    def test_list_active_excludes_completed(self, tmp_path):
        state = PipelineState(tmp_path)
        # Create two runs
        run1 = state.create_run("feat1", "branch1")
        run2 = state.create_run("feat2", "branch2")
        # Complete run1 (advance to EVOLVE)
        for _ in range(8):
            run1 = state.advance(run1.run_id)
        assert run1.current_stage == PipelineStage.EVOLVE
        active = state.list_active_runs()
        run_ids = [r.run_id for r in active]
        assert run2.run_id in run_ids
        assert run1.run_id not in run_ids

    def test_list_active_excludes_failed(self, tmp_path):
        state = PipelineState(tmp_path)
        run1 = state.create_run("feat1", "branch1")
        run2 = state.create_run("feat2", "branch2")
        state.fail(run1.run_id, "broke")
        active = state.list_active_runs()
        run_ids = [r.run_id for r in active]
        assert run2.run_id in run_ids
        assert run1.run_id not in run_ids

    def test_list_active_empty_dir(self, tmp_path):
        state = PipelineState(tmp_path)
        assert state.list_active_runs() == []


class TestAtomicWrite:
    def test_no_temp_files_left(self, tmp_path):
        """After write, no .tmp files should remain."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        state.advance(run.run_id)
        run_dir = tmp_path / ".pineapple" / "runs" / run.run_id
        tmp_files = list(run_dir.glob("*.tmp"))
        assert tmp_files == []


class TestPipelineTimeoutError:
    """TST-006: PipelineTimeoutError must be raised when wall-clock timeout is exceeded."""

    def test_advance_raises_on_timeout(self, tmp_path):
        """Create a run with zero timeout, then advance -- should raise PipelineTimeoutError."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")

        # Patch the run's wall_clock_timeout_hours to 0 and created_at to the past
        # so _check_timeout fires immediately on next advance()
        run_data = state.get_run(run.run_id)
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        # Write a modified state file with timeout=0 and created_at in the past
        import json
        state_file = tmp_path / ".pineapple" / "runs" / run.run_id / "state.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))
        data["wall_clock_timeout_hours"] = 0.0
        data["created_at"] = past_time
        state_file.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(PipelineTimeoutError) as exc_info:
            state.advance(run.run_id)

        error_msg = str(exc_info.value)
        assert run.run_id in error_msg
        assert "elapsed" in error_msg.lower() or "exceeded" in error_msg.lower()

    def test_retry_raises_on_timeout(self, tmp_path):
        """Retry should also check wall-clock timeout."""
        from datetime import datetime, timezone, timedelta
        import json

        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        # Advance to REVIEW (6 steps)
        for _ in range(6):
            run = state.advance(run.run_id)
        assert run.current_stage == PipelineStage.REVIEW

        # Now patch the state file to have expired timeout
        past_time = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        state_file = tmp_path / ".pineapple" / "runs" / run.run_id / "state.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))
        data["wall_clock_timeout_hours"] = 0.0
        data["created_at"] = past_time
        state_file.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(PipelineTimeoutError) as exc_info:
            state.retry(run.run_id, "retry after timeout")

        error_msg = str(exc_info.value)
        assert run.run_id in error_msg

    def test_timeout_error_contains_elapsed_time(self, tmp_path):
        """The PipelineTimeoutError message should include the elapsed time."""
        from datetime import datetime, timezone, timedelta
        import json

        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")

        # Set created_at to 5 hours ago, timeout to 2 hours
        past_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        state_file = tmp_path / ".pineapple" / "runs" / run.run_id / "state.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))
        data["wall_clock_timeout_hours"] = 2.0
        data["created_at"] = past_time
        state_file.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(PipelineTimeoutError) as exc_info:
            state.advance(run.run_id)

        error_msg = str(exc_info.value)
        # Should mention run_id
        assert run.run_id in error_msg
        # Should mention elapsed time (approximately 5.0h)
        assert "5." in error_msg or "elapsed" in error_msg.lower()
        # Should mention the timeout limit
        assert "2.0" in error_msg or "2h" in error_msg

    def test_no_timeout_when_within_limit(self, tmp_path):
        """When within timeout limit, advance should work normally."""
        state = PipelineState(tmp_path)
        run = state.create_run("feat", "branch")
        # Default timeout is 4 hours, run was just created, should be fine
        run = state.advance(run.run_id)
        assert run.current_stage == PipelineStage.BRAINSTORM
