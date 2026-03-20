"""Tests for pineapple_cleanup.py -- stale artifact detection and removal.

All tests use tmp_path fixtures and have no real filesystem dependencies.
"""

import json
import time
from datetime import datetime, timezone, timedelta

from pineapple_cleanup import (
    CleanupItem,
    CleanupReport,
    find_stale_runs,
    find_stale_verify_records,
    run_cleanup,
)


def _make_run(runs_dir, run_id, stage, days_old):
    """Helper: create a pipeline run state file with a given age."""
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    updated = datetime.now(timezone.utc) - timedelta(days=days_old)
    state = {
        "current_stage": stage,
        "updated_at": updated.isoformat(),
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run_dir


def _make_verify_record(verify_dir, name, days_old):
    """Helper: create a verification record with a given age."""
    ts = datetime.now(timezone.utc) - timedelta(days=days_old)
    data = {"timestamp": ts.isoformat(), "run_id": name}
    (verify_dir / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# CleanupReport dataclass
# ---------------------------------------------------------------------------


class TestCleanupReport:
    def test_empty_report_to_dict(self):
        report = CleanupReport()
        d = report.to_dict()
        assert d["dry_run"] is True
        assert d["stale_items"] == 0
        assert d["removed"] == 0
        assert d["items"] == []

    def test_report_with_items(self):
        report = CleanupReport(
            items=[CleanupItem("run", "/tmp/run1", "old", age_days=10.123)],
            dry_run=False,
        )
        d = report.to_dict()
        assert d["stale_items"] == 1
        assert d["items"][0]["age_days"] == 10.1


# ---------------------------------------------------------------------------
# find_stale_runs
# ---------------------------------------------------------------------------


class TestFindStaleRuns:
    def test_no_runs_dir(self, tmp_path):
        """Missing .pineapple/runs/ should return empty list."""
        assert find_stale_runs(tmp_path) == []

    def test_active_run_not_stale(self, tmp_path):
        """Runs in BUILD stage (not EVOLVE/FAILED) are never stale."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        _make_run(runs_dir, "active-run", "BUILD", days_old=30)
        result = find_stale_runs(tmp_path)
        assert len(result) == 0

    def test_failed_run_old_is_stale(self, tmp_path):
        """FAILED run older than 7 days should be flagged."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        _make_run(runs_dir, "old-fail", "FAILED", days_old=10)
        result = find_stale_runs(tmp_path)
        assert len(result) == 1
        assert result[0].category == "run"
        assert "FAILED" in result[0].reason

    def test_evolve_run_old_is_stale(self, tmp_path):
        """EVOLVE run older than 7 days should be flagged."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        _make_run(runs_dir, "old-evolve", "EVOLVE", days_old=15)
        result = find_stale_runs(tmp_path)
        assert len(result) == 1
        assert "EVOLVE" in result[0].reason

    def test_failed_run_recent_not_stale(self, tmp_path):
        """FAILED run under 7 days should not be flagged."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        _make_run(runs_dir, "recent-fail", "FAILED", days_old=3)
        result = find_stale_runs(tmp_path)
        assert len(result) == 0

    def test_custom_threshold(self, tmp_path):
        """Custom max_age_days threshold should be respected."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        _make_run(runs_dir, "run1", "FAILED", days_old=2)
        assert find_stale_runs(tmp_path, max_age_days=1.0) != []
        assert find_stale_runs(tmp_path, max_age_days=3.0) == []

    def test_multiple_stale_runs(self, tmp_path):
        """Multiple stale runs should all be found."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        _make_run(runs_dir, "r1", "FAILED", days_old=10)
        _make_run(runs_dir, "r2", "EVOLVE", days_old=20)
        _make_run(runs_dir, "r3", "BUILD", days_old=100)  # active, ignored
        result = find_stale_runs(tmp_path)
        assert len(result) == 2

    def test_run_without_state_file_skipped(self, tmp_path):
        """Run directory with no state.json should be ignored."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        (runs_dir / "empty-run").mkdir(parents=True)
        result = find_stale_runs(tmp_path)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# find_stale_verify_records
# ---------------------------------------------------------------------------


class TestFindStaleVerifyRecords:
    def test_no_verify_dir(self, tmp_path):
        assert find_stale_verify_records(tmp_path) == []

    def test_recent_record_not_stale(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_verify_record(verify_dir, "recent", days_old=5)
        result = find_stale_verify_records(tmp_path)
        assert len(result) == 0

    def test_old_record_is_stale(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_verify_record(verify_dir, "old", days_old=45)
        result = find_stale_verify_records(tmp_path)
        assert len(result) == 1
        assert result[0].category == "verify_record"
        assert result[0].age_days > 30

    def test_custom_threshold(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_verify_record(verify_dir, "mid", days_old=10)
        assert find_stale_verify_records(tmp_path, max_age_days=5.0) != []
        assert find_stale_verify_records(tmp_path, max_age_days=15.0) == []

    def test_multiple_records_mixed(self, tmp_path):
        """Only old records are flagged, recent ones are kept."""
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_verify_record(verify_dir, "old1", days_old=60)
        _make_verify_record(verify_dir, "old2", days_old=90)
        _make_verify_record(verify_dir, "recent", days_old=5)
        result = find_stale_verify_records(tmp_path)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# run_cleanup (integration)
# ---------------------------------------------------------------------------


class TestRunCleanupDryRun:
    def test_dry_run_does_not_delete(self, tmp_path):
        """Dry run should flag items but not remove anything."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        run_dir = _make_run(runs_dir, "stale", "FAILED", days_old=20)

        report = run_cleanup(tmp_path, dry_run=True)
        assert len(report.items) == 1
        assert len(report.removed) == 0
        # Directory still exists
        assert run_dir.is_dir()

    def test_dry_run_is_default(self, tmp_path):
        """run_cleanup defaults to dry_run=True."""
        report = run_cleanup(tmp_path)
        assert report.dry_run is True


class TestRunCleanupExecute:
    def test_execute_removes_stale_run(self, tmp_path):
        """Execute mode should actually delete stale run directories."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        run_dir = _make_run(runs_dir, "stale", "FAILED", days_old=20)
        assert run_dir.is_dir()

        report = run_cleanup(tmp_path, dry_run=False)
        assert len(report.items) == 1
        assert len(report.removed) == 1
        assert not run_dir.is_dir()

    def test_execute_removes_stale_verify_record(self, tmp_path):
        """Execute mode should delete stale verification records."""
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_verify_record(verify_dir, "ancient", days_old=60)
        record_path = verify_dir / "ancient.json"
        assert record_path.is_file()

        report = run_cleanup(tmp_path, dry_run=False)
        assert len(report.removed) == 1
        assert not record_path.is_file()


class TestRunCleanupCleanProject:
    def test_no_stale_artifacts(self, tmp_path):
        """Clean project should report zero items."""
        runs_dir = tmp_path / ".pineapple" / "runs"
        _make_run(runs_dir, "active", "BUILD", days_old=1)
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_verify_record(verify_dir, "recent", days_old=2)

        report = run_cleanup(tmp_path)
        assert len(report.items) == 0
        assert len(report.removed) == 0
