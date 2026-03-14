"""Tests for pineapple_evolve.py — post-session automation."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch
from pineapple_evolve import (
    StepResult, EvolveReport, run_evolve,
    step_1_session_handoff, step_2_update_bible,
    step_3_append_decisions,
)


class TestStepResult:
    def test_done_result(self):
        r = StepResult(name="test", status="done", message="ok")
        assert r.status == "done"


class TestEvolveReport:
    def test_all_done(self):
        report = EvolveReport(steps=[
            StepResult("a", "done", "ok"),
            StepResult("b", "skip", "optional"),
        ])
        assert report.all_done is True

    def test_partial_on_error(self):
        report = EvolveReport(steps=[
            StepResult("a", "done", "ok"),
            StepResult("b", "error", "failed"),
        ])
        assert report.all_done is False

    def test_to_dict(self):
        report = EvolveReport(steps=[StepResult("a", "done", "ok")])
        d = report.to_dict()
        assert d["overall"] == "done"
        assert len(d["steps"]) == 1


class TestSessionHandoff:
    @patch("subprocess.run")
    def test_generates_from_git(self, mock_run, tmp_path):
        mock_run.return_value = type("Result", (), {"stdout": "abc123 Some commit\ndef456 Another commit", "returncode": 0})()
        result = step_1_session_handoff(tmp_path)
        assert result.status == "done"
        # Check file was written
        sessions = list((tmp_path / "sessions").glob("*.md"))
        assert len(sessions) == 1

    @patch("subprocess.run")
    def test_skips_no_commits(self, mock_run, tmp_path):
        mock_run.return_value = type("Result", (), {"stdout": "", "returncode": 0})()
        result = step_1_session_handoff(tmp_path)
        assert result.status == "skip"

    def test_uses_provided_file(self, tmp_path):
        session_file = tmp_path / "session.md"
        session_file.write_text("# Session")
        result = step_1_session_handoff(tmp_path, session_file=session_file)
        assert result.status == "done"


class TestUpdateBible:
    def test_skip_no_projects_dir(self, tmp_path):
        result = step_2_update_bible(tmp_path)
        assert result.status == "skip"

    def test_skip_no_bible(self, tmp_path):
        (tmp_path / "projects").mkdir()
        result = step_2_update_bible(tmp_path)
        assert result.status == "skip"


class TestAppendDecisions:
    def test_skip_no_decisions(self, tmp_path):
        result = step_3_append_decisions(tmp_path)
        assert result.status == "skip"

    def test_appends_to_existing(self, tmp_path):
        decisions_file = tmp_path / "memory" / "decisions.md"
        decisions_file.parent.mkdir(parents=True)
        decisions_file.write_text("# Decisions\n")
        result = step_3_append_decisions(tmp_path, decisions="Use CadQuery for gears")
        assert result.status == "done"
        content = decisions_file.read_text()
        assert "Use CadQuery for gears" in content

    def test_creates_new_file(self, tmp_path):
        result = step_3_append_decisions(tmp_path, decisions="First decision")
        assert result.status == "done"


class TestRunEvolve:
    @patch("subprocess.run")
    def test_run_evolve_returns_report(self, mock_run, tmp_path):
        mock_run.return_value = type("Result", (), {"stdout": "abc123 commit", "returncode": 0})()
        report = run_evolve(tmp_path)
        assert isinstance(report, EvolveReport)
        assert len(report.steps) == 6

    @patch("subprocess.run")
    def test_run_evolve_all_steps_independent(self, mock_run, tmp_path):
        """Each step should run even if previous steps fail."""
        mock_run.side_effect = Exception("git broke")
        report = run_evolve(tmp_path)
        # Should still have 6 steps (even if some errored/skipped)
        assert len(report.steps) == 6
