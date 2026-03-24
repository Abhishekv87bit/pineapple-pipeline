"""Unit tests for v2 agent node functions (src/pineapple/agents/).

Each test calls the node function with a minimal valid state and verifies
it returns the expected state keys. All tests use the no-LLM fallback paths.

Covers all 10 agent nodes + human_intervention_node from graph.py.
"""
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> dict:
    """Create a minimal PipelineState dict with sensible defaults."""
    base = {
        "run_id": "test-run-001",
        "request": "Build a test widget",
        "project_name": "test-widget",
        "branch": "main",
        "path": "full",
        "current_stage": "intake",
        "context_bundle": None,
        "strategic_brief": None,
        "design_spec": None,
        "task_plan": None,
        "workspace_info": None,
        "build_results": [],
        "verify_record": None,
        "review_result": None,
        "ship_result": None,
        "evolve_report": None,
        "attempt_counts": {},
        "human_approvals": {},
        "cost_total_usd": 0.0,
        "errors": [],
        "messages": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Stage 0: Intake
# ---------------------------------------------------------------------------


class TestIntakeNode:
    """intake_node is pure Python, no LLM required."""

    def test_returns_current_stage(self):
        from pineapple.agents.intake import intake_node

        result = intake_node(_make_state())
        assert result["current_stage"] == "intake"

    def test_returns_context_bundle(self):
        from pineapple.agents.intake import intake_node

        result = intake_node(_make_state())
        assert "context_bundle" in result
        bundle = result["context_bundle"]
        assert "project_type" in bundle
        assert "context_files" in bundle
        assert "classification" in bundle

    def test_returns_project_name(self):
        from pineapple.agents.intake import intake_node

        result = intake_node(_make_state(project_name="my-project"))
        assert result["project_name"] == "my-project"

    def test_auto_generates_project_name(self):
        from pineapple.agents.intake import intake_node

        result = intake_node(_make_state(project_name=""))
        assert result["project_name"]  # should be auto-generated slug
        assert isinstance(result["project_name"], str)

    def test_bug_fix_classified_correctly(self):
        from pineapple.agents.intake import intake_node

        result = intake_node(_make_state(request="Fix the broken login page"))
        bundle = result["context_bundle"]
        assert bundle["project_type"] == "bug_fix"

    def test_new_project_classified_correctly(self):
        from pineapple.agents.intake import intake_node

        result = intake_node(_make_state(request="Create a new dashboard"))
        bundle = result["context_bundle"]
        assert bundle["project_type"] == "new_project"

    def test_user_path_preserved(self):
        """If user set path='lightweight', intake should not override it."""
        from pineapple.agents.intake import intake_node

        result = intake_node(_make_state(path="lightweight"))
        # intake only sets path if user_path is None/empty
        assert "path" not in result or result.get("path") == "lightweight"


# ---------------------------------------------------------------------------
# Stage 1: Strategic Review
# ---------------------------------------------------------------------------


class TestStrategicReviewNode:
    """strategic_review_node falls back gracefully without LLM deps/keys."""

    def test_no_llm_deps_returns_error_brief(self):
        from pineapple.agents.strategic_review import strategic_review_node

        state = _make_state(
            context_bundle={
                "project_type": "new_project",
                "context_files": [],
                "classification": "test",
            }
        )

        with patch("pineapple.agents.strategic_review._HAS_LLM_DEPS", False):
            result = strategic_review_node(state)

        assert result["current_stage"] == "strategic_review"
        assert result["strategic_brief"] is not None
        assert "error" in result["strategic_brief"]["what"].lower() or "not available" in result["strategic_brief"]["what"].lower()
        assert len(result.get("errors", [])) > 0

    def test_no_api_key_returns_error_brief(self):
        from pineapple.agents.strategic_review import strategic_review_node

        state = _make_state(
            context_bundle={
                "project_type": "new_project",
                "context_files": [],
                "classification": "test",
            }
        )

        with (
            patch("pineapple.agents.strategic_review._HAS_LLM_DEPS", True),
            patch("pineapple.agents.strategic_review.has_any_llm_key", return_value=False),
        ):
            result = strategic_review_node(state)

        assert result["current_stage"] == "strategic_review"
        assert result["strategic_brief"] is not None
        assert len(result.get("errors", [])) > 0

    def test_output_has_expected_keys(self):
        from pineapple.agents.strategic_review import strategic_review_node

        state = _make_state()
        with patch("pineapple.agents.strategic_review._HAS_LLM_DEPS", False):
            result = strategic_review_node(state)

        assert "current_stage" in result
        assert "strategic_brief" in result
        brief = result["strategic_brief"]
        assert "what" in brief
        assert "why" in brief
        assert "not_building" in brief
        assert "who_benefits" in brief


# ---------------------------------------------------------------------------
# Stage 2: Architecture
# ---------------------------------------------------------------------------


class TestArchitectureNode:
    """architecture_node falls back gracefully without LLM deps/keys."""

    def test_no_llm_deps_returns_error_spec(self):
        from pineapple.agents.architecture import architecture_node

        state = _make_state(
            strategic_brief={
                "what": "Test project",
                "why": "Testing",
                "not_building": [],
                "who_benefits": "testers",
                "assumptions": [],
                "open_questions": [],
                "approved": True,
            }
        )

        with patch("pineapple.agents.architecture._HAS_LLM_DEPS", False):
            result = architecture_node(state)

        assert result["current_stage"] == "architecture"
        assert result["design_spec"] is not None
        assert "error" in result["design_spec"]["title"].lower()
        assert len(result.get("errors", [])) > 0

    def test_no_api_key_returns_error_spec(self):
        from pineapple.agents.architecture import architecture_node

        state = _make_state(
            strategic_brief={"what": "Test", "why": "Test"}
        )

        with (
            patch("pineapple.agents.architecture._HAS_LLM_DEPS", True),
            patch("pineapple.agents.architecture.has_any_llm_key", return_value=False),
        ):
            result = architecture_node(state)

        assert result["current_stage"] == "architecture"
        assert result["design_spec"] is not None

    def test_missing_strategic_brief_returns_error(self):
        from pineapple.agents.architecture import architecture_node

        state = _make_state(strategic_brief=None)

        with (
            patch("pineapple.agents.architecture._HAS_LLM_DEPS", True),
            patch("pineapple.agents.architecture.has_any_llm_key", return_value=True),
        ):
            result = architecture_node(state)

        assert result["current_stage"] == "architecture"
        assert len(result.get("errors", [])) > 0

    def test_output_has_expected_keys(self):
        from pineapple.agents.architecture import architecture_node

        state = _make_state()
        with patch("pineapple.agents.architecture._HAS_LLM_DEPS", False):
            result = architecture_node(state)

        spec = result["design_spec"]
        assert "title" in spec
        assert "summary" in spec
        assert "components" in spec
        assert "technology_choices" in spec


# ---------------------------------------------------------------------------
# Stage 3: Plan
# ---------------------------------------------------------------------------


class TestPlanNode:
    """plan_node falls back gracefully without LLM deps/keys."""

    def test_no_llm_deps_returns_error_plan(self):
        from pineapple.agents.planner import plan_node

        state = _make_state(
            design_spec={
                "title": "Test Arch",
                "summary": "Test",
                "components": [],
                "technology_choices": {},
            }
        )

        with patch("pineapple.agents.planner._HAS_LLM_DEPS", False):
            result = plan_node(state)

        assert result["current_stage"] == "plan"
        assert result["task_plan"] is not None
        assert isinstance(result["task_plan"]["tasks"], list)
        assert len(result.get("errors", [])) > 0

    def test_no_api_key_returns_error_plan(self):
        from pineapple.agents.planner import plan_node

        state = _make_state(design_spec={"title": "T", "summary": "S"})

        with (
            patch("pineapple.agents.planner._HAS_LLM_DEPS", True),
            patch("pineapple.agents.planner.has_any_llm_key", return_value=False),
        ):
            result = plan_node(state)

        assert result["current_stage"] == "plan"
        assert result["task_plan"] is not None

    def test_output_has_expected_keys(self):
        from pineapple.agents.planner import plan_node

        state = _make_state()
        with patch("pineapple.agents.planner._HAS_LLM_DEPS", False):
            result = plan_node(state)

        plan = result["task_plan"]
        assert "tasks" in plan
        assert "total_estimated_cost_usd" in plan


# ---------------------------------------------------------------------------
# Stage 4: Setup
# ---------------------------------------------------------------------------


class TestSetupNode:
    """setup_node is pure Python. Tests avoid actual git/filesystem ops."""

    def test_returns_current_stage(self, tmp_path, monkeypatch):
        from pineapple.agents.setup import setup_node

        monkeypatch.chdir(tmp_path)
        state = _make_state()
        result = setup_node(state)
        assert result["current_stage"] == "setup"

    def test_returns_workspace_info(self, tmp_path, monkeypatch):
        from pineapple.agents.setup import setup_node

        monkeypatch.chdir(tmp_path)
        state = _make_state()
        result = setup_node(state)
        assert "workspace_info" in result
        info = result["workspace_info"]
        assert "run_dir" in info
        assert "tools_available" in info
        assert "scaffolded_files" in info

    def test_creates_run_dir(self, tmp_path, monkeypatch):
        from pineapple.agents.setup import setup_node

        monkeypatch.chdir(tmp_path)
        state = _make_state(run_id="setup-test-run")
        result = setup_node(state)
        run_dir = result["workspace_info"]["run_dir"]
        assert "setup-test-run" in run_dir

    def test_no_task_plan_skips_scaffolding(self, tmp_path, monkeypatch):
        from pineapple.agents.setup import setup_node

        monkeypatch.chdir(tmp_path)
        state = _make_state(task_plan=None)
        result = setup_node(state)
        assert result["workspace_info"]["scaffolded_files"] == []


# ---------------------------------------------------------------------------
# Stage 5: Builder
# ---------------------------------------------------------------------------


class TestBuilderNode:
    """builder_node falls back to placeholder builds without LLM."""

    def test_fallback_builds_all_tasks(self):
        from pineapple.agents.builder import builder_node

        state = _make_state(
            task_plan={
                "tasks": [
                    {"id": "T1", "description": "Do task 1", "complexity": "trivial", "estimated_cost_usd": 0.01},
                    {"id": "T2", "description": "Do task 2", "complexity": "standard", "estimated_cost_usd": 0.02},
                ],
                "total_estimated_cost_usd": 0.03,
            }
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        assert result["current_stage"] == "build"
        assert len(result["build_results"]) == 2
        assert all(r["status"] == "completed" for r in result["build_results"])

    def test_lightweight_path_auto_generates_task(self):
        from pineapple.agents.builder import builder_node

        state = _make_state(
            task_plan=None,
            request="Fix the typo in README",
            path="lightweight",
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        assert result["current_stage"] == "build"
        assert len(result["build_results"]) == 1

    def test_increments_attempt_count(self):
        from pineapple.agents.builder import builder_node

        state = _make_state(
            task_plan={
                "tasks": [{"id": "T1", "description": "test", "complexity": "trivial", "estimated_cost_usd": 0.0}],
                "total_estimated_cost_usd": 0.0,
            },
            attempt_counts={"build": 1},
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        assert result["attempt_counts"]["build"] == 2

    def test_output_has_expected_keys(self):
        from pineapple.agents.builder import builder_node

        state = _make_state(task_plan=None)
        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        assert "current_stage" in result
        assert "build_results" in result
        assert "attempt_counts" in result


# ---------------------------------------------------------------------------
# Stage 6: Verifier
# ---------------------------------------------------------------------------


class TestVerifierNode:
    """verifier_node is pure Python. Test with mocked subprocess calls."""

    def test_returns_current_stage(self, tmp_path, monkeypatch):
        from pineapple.agents.verifier import verifier_node

        monkeypatch.chdir(tmp_path)
        state = _make_state()
        result = verifier_node(state)
        assert result["current_stage"] == "verify"

    def test_returns_verify_record(self, tmp_path, monkeypatch):
        from pineapple.agents.verifier import verifier_node

        monkeypatch.chdir(tmp_path)
        state = _make_state()
        result = verifier_node(state)
        assert "verify_record" in result
        record = result["verify_record"]
        assert "all_green" in record
        assert "layers" in record
        assert isinstance(record["layers"], list)
        assert len(record["layers"]) == 6  # 6 verification layers

    def test_all_layers_have_expected_fields(self, tmp_path, monkeypatch):
        from pineapple.agents.verifier import verifier_node

        monkeypatch.chdir(tmp_path)
        state = _make_state()
        result = verifier_node(state)
        for layer in result["verify_record"]["layers"]:
            assert "layer" in layer
            assert "name" in layer
            assert "status" in layer
            assert layer["status"] in ("pass", "fail", "skip")


# ---------------------------------------------------------------------------
# Stage 7: Reviewer
# ---------------------------------------------------------------------------


class TestReviewerNode:
    """reviewer_node falls back to heuristic review without LLM."""

    def test_fallback_pass_when_all_completed(self):
        from pineapple.agents.reviewer import reviewer_node

        state = _make_state(
            build_results=[
                {"task_id": "T1", "status": "completed", "commits": ["abc"], "errors": []},
            ],
            verify_record={"all_green": True, "layers": []},
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        assert result["current_stage"] == "review"
        assert result["review_result"]["verdict"] == "pass"

    def test_fallback_retry_when_build_failed(self):
        from pineapple.agents.reviewer import reviewer_node

        state = _make_state(
            build_results=[
                {"task_id": "T1", "status": "failed", "commits": [], "errors": ["compilation error"]},
            ],
            verify_record={"all_green": False, "layers": []},
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        assert result["current_stage"] == "review"
        assert result["review_result"]["verdict"] == "retry"

    def test_fallback_retry_when_verification_fails(self):
        from pineapple.agents.reviewer import reviewer_node

        state = _make_state(
            build_results=[
                {"task_id": "T1", "status": "completed", "commits": ["abc"], "errors": []},
            ],
            verify_record={"all_green": False, "layers": []},
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        assert result["current_stage"] == "review"
        assert result["review_result"]["verdict"] == "retry"

    def test_lightweight_path_passes_with_minimal_build(self):
        from pineapple.agents.reviewer import reviewer_node

        state = _make_state(
            path="lightweight",
            build_results=[
                {"task_id": "T1", "status": "completed", "commits": [], "errors": []},
            ],
            verify_record=None,
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        assert result["review_result"]["verdict"] == "pass"

    def test_output_has_expected_keys(self):
        from pineapple.agents.reviewer import reviewer_node

        state = _make_state(build_results=[], verify_record=None)
        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        rr = result["review_result"]
        assert "verdict" in rr
        assert "critical_issues" in rr
        assert "important_issues" in rr
        assert "minor_issues" in rr


# ---------------------------------------------------------------------------
# Stage 8: Shipper
# ---------------------------------------------------------------------------


class TestShipperNode:
    """ship_node is pure Python. Tests avoid actual git operations."""

    def test_keep_when_no_review_result(self):
        from pineapple.agents.shipper import ship_node

        state = _make_state(review_result=None)
        result = ship_node(state)
        assert result["current_stage"] == "ship"
        assert result["ship_result"]["action"] == "keep"

    def test_keep_when_review_failed(self):
        from pineapple.agents.shipper import ship_node

        state = _make_state(
            review_result={
                "verdict": "retry",
                "critical_issues": ["bug found"],
                "important_issues": [],
                "minor_issues": [],
            }
        )
        result = ship_node(state)
        assert result["ship_result"]["action"] == "keep"

    def test_keep_on_lightweight_path_even_when_passed(self):
        from pineapple.agents.shipper import ship_node

        state = _make_state(
            path="lightweight",
            review_result={
                "verdict": "pass",
                "critical_issues": [],
                "important_issues": [],
                "minor_issues": [],
            },
        )
        result = ship_node(state)
        assert result["ship_result"]["action"] == "keep"

    def test_pr_on_full_path_when_passed(self):
        """Full path + pass verdict should attempt PR (falls back to keep without git)."""
        from pineapple.agents.shipper import ship_node

        state = _make_state(
            path="full",
            review_result={
                "verdict": "pass",
                "critical_issues": [],
                "important_issues": [],
                "minor_issues": [],
            },
        )
        result = ship_node(state)
        # Will fall back to "keep" because gh/git not available in test env,
        # but the action determination should have been "pr"
        assert result["current_stage"] == "ship"
        assert result["ship_result"]["action"] in ("pr", "keep")

    def test_output_has_expected_keys(self):
        from pineapple.agents.shipper import ship_node

        state = _make_state(review_result=None)
        result = ship_node(state)
        sr = result["ship_result"]
        assert "action" in sr
        assert "pr_url" in sr
        assert "merge_commit" in sr


# ---------------------------------------------------------------------------
# Stage 9: Evolver
# ---------------------------------------------------------------------------


class TestEvolverNode:
    """evolve_node is pure Python, no LLM required."""

    def test_returns_current_stage(self):
        from pineapple.agents.evolver import evolve_node

        result = evolve_node(_make_state())
        assert result["current_stage"] == "evolve"

    def test_returns_evolve_report(self):
        from pineapple.agents.evolver import evolve_node

        result = evolve_node(_make_state())
        assert "evolve_report" in result
        report = result["evolve_report"]
        assert "session_handoff_path" in report
        assert "bible_updated" in report
        assert "decisions_logged" in report

    def test_captures_decisions_from_build_results(self):
        from pineapple.agents.evolver import evolve_node

        state = _make_state(
            build_results=[
                {"task_id": "T1", "status": "completed", "commits": [], "errors": []},
                {"task_id": "T2", "status": "failed", "commits": [], "errors": ["err"]},
            ],
            verify_record={"all_green": True, "layers": []},
            review_result={"verdict": "pass"},
            ship_result={"action": "keep"},
        )
        result = evolve_node(state)
        decisions = result["evolve_report"]["decisions_logged"]
        assert len(decisions) >= 1  # at least build summary

    def test_handoff_path_contains_project_name(self):
        from pineapple.agents.evolver import evolve_node

        result = evolve_node(_make_state(project_name="my-cool-project"))
        path = result["evolve_report"]["session_handoff_path"]
        assert "my-cool-project" in path


# ---------------------------------------------------------------------------
# Human Intervention Node (graph.py)
# ---------------------------------------------------------------------------


class TestHumanInterventionNode:
    """human_intervention_node is defined in graph.py, not agents/."""

    def test_returns_current_stage(self):
        from pineapple.graph import human_intervention_node

        state = _make_state(
            review_result={
                "verdict": "fail",
                "critical_issues": ["fatal bug"],
                "important_issues": [],
                "minor_issues": [],
            }
        )
        result = human_intervention_node(state)
        assert result["current_stage"] == "human_intervention"

    def test_handles_no_review_result(self):
        from pineapple.graph import human_intervention_node

        state = _make_state(review_result=None)
        result = human_intervention_node(state)
        assert result["current_stage"] == "human_intervention"

    def test_handles_empty_errors(self):
        from pineapple.graph import human_intervention_node

        state = _make_state(errors=[])
        result = human_intervention_node(state)
        assert result["current_stage"] == "human_intervention"
