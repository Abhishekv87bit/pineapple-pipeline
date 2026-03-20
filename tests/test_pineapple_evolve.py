"""Tests for pineapple_evolve.py -- post-session automation."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pineapple_evolve import (
    EvolveReport,
    StepResult,
    _extract_facts_from_session,
    run_evolve,
    step_1_session_handoff,
    step_2_update_bible,
    step_3_append_decisions,
    step_4_feed_mem0,
    step_5_feed_neo4j,
    step_6_update_eval_baselines,
)


class TestStepResult:
    def test_done_result(self):
        r = StepResult(name="test", status="done", message="ok")
        assert r.status == "done"


class TestEvolveReport:
    def test_all_done(self):
        report = EvolveReport(
            steps=[
                StepResult("a", "done", "ok"),
                StepResult("b", "skip", "optional"),
            ]
        )
        assert report.all_done is True

    def test_partial_on_error(self):
        report = EvolveReport(
            steps=[
                StepResult("a", "done", "ok"),
                StepResult("b", "error", "failed"),
            ]
        )
        assert report.all_done is False

    def test_to_dict(self):
        report = EvolveReport(steps=[StepResult("a", "done", "ok")])
        d = report.to_dict()
        assert d["overall"] == "done"
        assert len(d["steps"]) == 1


class TestSessionHandoff:
    @patch("subprocess.run")
    def test_generates_from_git(self, mock_run, tmp_path):
        mock_run.return_value = type(
            "Result", (), {"stdout": "abc123 Some commit\ndef456 Another commit", "returncode": 0}
        )()
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


# ---------------------------------------------------------------------------
# Step 4: feed_mem0
# ---------------------------------------------------------------------------


class TestFeedMem0:
    """Tests for step_4_feed_mem0 using mocked httpx."""

    def _make_session_file(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sf = sessions_dir / "2026-03-20.md"
        sf.write_text(
            "# Session 2026-03-20\n"
            "## Decisions\n"
            "- Use CadQuery for gear generation\n"
            "- Single motor constraint confirmed\n",
            encoding="utf-8",
        )
        return sf

    @patch("pineapple_evolve._httpx")
    def test_success_stores_facts(self, mock_httpx, tmp_path):
        sf = self._make_session_file(tmp_path)

        # Health check passes
        health_resp = MagicMock()
        health_resp.raise_for_status = MagicMock()
        # POST memories passes
        mem_resp = MagicMock()
        mem_resp.raise_for_status = MagicMock()

        mock_httpx.get.return_value = health_resp
        mock_httpx.post.return_value = mem_resp
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException

        result = step_4_feed_mem0(tmp_path, session_file=sf)

        assert result.status == "done"
        assert "Stored" in result.message
        assert mock_httpx.post.called

    @patch("pineapple_evolve._httpx")
    def test_connection_error_returns_skip(self, mock_httpx, tmp_path):
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException
        mock_httpx.get.side_effect = httpx.ConnectError("refused")

        result = step_4_feed_mem0(tmp_path)

        assert result.status == "skip"
        assert "not reachable" in result.message.lower()

    @patch("pineapple_evolve._httpx")
    def test_timeout_returns_skip(self, mock_httpx, tmp_path):
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException
        mock_httpx.get.side_effect = httpx.TimeoutException("timed out")

        result = step_4_feed_mem0(tmp_path)

        assert result.status == "skip"
        assert "timed out" in result.message.lower()

    @patch("pineapple_evolve._httpx")
    def test_no_facts_skips_posting(self, mock_httpx, tmp_path):
        """Empty session file -> skip after health check succeeds."""
        health_resp = MagicMock()
        health_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = health_resp
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException

        result = step_4_feed_mem0(tmp_path)  # no session file, no sessions/ dir

        assert result.status == "skip"
        assert "No facts" in result.message
        mock_httpx.post.assert_not_called()


# ---------------------------------------------------------------------------
# Step 5: feed_neo4j
# ---------------------------------------------------------------------------


class TestFeedNeo4j:
    """Tests for step_5_feed_neo4j using mocked httpx."""

    def _make_session_with_relationships(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sf = sessions_dir / "2026-03-20.md"
        sf.write_text(
            "# Session\n"
            "- modified: TripleHelixShaft\n"
            "- PlanetaryGear depends on SunGear\n",
            encoding="utf-8",
        )
        return sf

    @patch("pineapple_evolve._httpx")
    def test_success_sends_relationships(self, mock_httpx, tmp_path):
        self._make_session_with_relationships(tmp_path)

        health_resp = MagicMock()
        tx_resp = MagicMock()
        tx_resp.raise_for_status = MagicMock()
        tx_resp.json.return_value = {"errors": [], "results": []}

        mock_httpx.get.return_value = health_resp
        mock_httpx.post.return_value = tx_resp
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException

        result = step_5_feed_neo4j(tmp_path)

        assert result.status == "done"
        assert "relationship" in result.message.lower()
        mock_httpx.post.assert_called_once()

    @patch("pineapple_evolve._httpx")
    def test_connection_error_returns_skip(self, mock_httpx, tmp_path):
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException
        mock_httpx.get.side_effect = httpx.ConnectError("refused")

        result = step_5_feed_neo4j(tmp_path)

        assert result.status == "skip"
        assert "not reachable" in result.message.lower()

    @patch("pineapple_evolve._httpx")
    def test_neo4j_error_response_returns_error(self, mock_httpx, tmp_path):
        self._make_session_with_relationships(tmp_path)

        health_resp = MagicMock()
        tx_resp = MagicMock()
        tx_resp.raise_for_status = MagicMock()
        tx_resp.json.return_value = {"errors": [{"message": "Syntax error"}], "results": []}

        mock_httpx.get.return_value = health_resp
        mock_httpx.post.return_value = tx_resp
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException

        result = step_5_feed_neo4j(tmp_path)

        assert result.status == "error"
        assert "Syntax error" in result.message

    @patch("pineapple_evolve._httpx")
    def test_no_relationships_skips_tx(self, mock_httpx, tmp_path):
        """No session file -> no relationships -> skip after health check."""
        health_resp = MagicMock()
        mock_httpx.get.return_value = health_resp
        mock_httpx.ConnectError = httpx.ConnectError
        mock_httpx.TimeoutException = httpx.TimeoutException

        result = step_5_feed_neo4j(tmp_path)

        assert result.status == "skip"
        assert "No component relationships" in result.message
        mock_httpx.post.assert_not_called()


# ---------------------------------------------------------------------------
# Step 6: update_eval_baselines
# ---------------------------------------------------------------------------


def _write_verify_record(verify_dir, name, test_count, layers_passed):
    verify_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "version": "1.0.0",
        "test_count": test_count,
        "layers_passed": layers_passed,
        "layers_failed": [],
        "all_green": True,
    }
    (verify_dir / name).write_text(json.dumps(record), encoding="utf-8")


class TestUpdateEvalBaselines:
    def test_skip_no_verify_dir(self, tmp_path):
        result = step_6_update_eval_baselines(tmp_path)
        assert result.status == "skip"
        assert ".pineapple/verify/" in result.message

    def test_skip_no_records(self, tmp_path):
        (tmp_path / ".pineapple" / "verify").mkdir(parents=True)
        result = step_6_update_eval_baselines(tmp_path)
        assert result.status == "skip"

    def test_creates_baseline_when_none_exists(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        _write_verify_record(verify_dir, "run1.json", test_count=42, layers_passed=[1, 2, 3])

        result = step_6_update_eval_baselines(tmp_path)

        assert result.status == "done"
        assert "Baseline created" in result.message
        baselines = json.loads((tmp_path / ".pineapple" / "baselines.json").read_text())
        assert baselines["test_count"] == 42
        assert baselines["layers_passed_count"] == 3

    def test_updates_baseline_on_improvement(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        _write_verify_record(verify_dir, "run1.json", test_count=50, layers_passed=[1, 2, 3, 4])

        # Write existing baseline with lower numbers
        baselines_path = tmp_path / ".pineapple" / "baselines.json"
        baselines_path.parent.mkdir(parents=True, exist_ok=True)
        baselines_path.write_text(
            json.dumps({"test_count": 30, "layers_passed_count": 2}), encoding="utf-8"
        )

        result = step_6_update_eval_baselines(tmp_path)

        assert result.status == "done"
        assert "Improved" in result.message
        updated = json.loads(baselines_path.read_text())
        assert updated["test_count"] == 50
        assert updated["layers_passed_count"] == 4

    def test_skip_when_unchanged(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        _write_verify_record(verify_dir, "run1.json", test_count=30, layers_passed=[1, 2])

        baselines_path = tmp_path / ".pineapple" / "baselines.json"
        baselines_path.parent.mkdir(parents=True, exist_ok=True)
        baselines_path.write_text(
            json.dumps({"test_count": 30, "layers_passed_count": 2}), encoding="utf-8"
        )

        result = step_6_update_eval_baselines(tmp_path)

        assert result.status == "skip"
        assert "No change" in result.message

    def test_error_on_regression(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        _write_verify_record(verify_dir, "run1.json", test_count=20, layers_passed=[1])

        baselines_path = tmp_path / ".pineapple" / "baselines.json"
        baselines_path.parent.mkdir(parents=True, exist_ok=True)
        baselines_path.write_text(
            json.dumps({"test_count": 30, "layers_passed_count": 3}), encoding="utf-8"
        )

        result = step_6_update_eval_baselines(tmp_path)

        assert result.status == "error"
        assert "REGRESSION" in result.message
        # Baseline must NOT have been updated
        unchanged = json.loads(baselines_path.read_text())
        assert unchanged["test_count"] == 30
