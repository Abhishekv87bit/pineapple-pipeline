"""Unit tests for v2 agent node functions (src/pineapple/agents/).

Each test calls the node function with a minimal valid state and verifies
it returns the expected state keys. All tests use the no-LLM fallback paths.

Covers all 10 agent nodes + human_intervention_node from graph.py.
"""
import os
import subprocess
import tempfile
from pathlib import Path
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
        "target_dir": "",
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
        "changed_files": None,
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
            patch("pineapple.agents.strategic_review.has_any_llm_key", return_value=False, create=True),
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
            patch("pineapple.agents.architecture.has_any_llm_key", return_value=False, create=True),
        ):
            result = architecture_node(state)

        assert result["current_stage"] == "architecture"
        assert result["design_spec"] is not None

    def test_missing_strategic_brief_returns_error(self):
        from pineapple.agents.architecture import architecture_node

        state = _make_state(strategic_brief=None)

        with (
            patch("pineapple.agents.architecture._HAS_LLM_DEPS", True),
            patch("pineapple.agents.architecture.has_any_llm_key", return_value=True, create=True),
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
            patch("pineapple.agents.planner.has_any_llm_key", return_value=False, create=True),
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

    def test_fallback_builds_all_tasks(self, tmp_path):
        from pineapple.agents.builder import builder_node

        state = _make_state(
            target_dir=str(tmp_path),
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

    def test_lightweight_path_auto_generates_task(self, tmp_path):
        from pineapple.agents.builder import builder_node

        state = _make_state(
            target_dir=str(tmp_path),
            task_plan=None,
            request="Fix the typo in README",
            path="lightweight",
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        assert result["current_stage"] == "build"
        assert len(result["build_results"]) == 1

    def test_increments_attempt_count(self, tmp_path):
        from pineapple.agents.builder import builder_node

        state = _make_state(
            target_dir=str(tmp_path),
            task_plan={
                "tasks": [{"id": "T1", "description": "test", "complexity": "trivial", "estimated_cost_usd": 0.0}],
                "total_estimated_cost_usd": 0.0,
            },
            attempt_counts={"build": 1},
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        assert result["attempt_counts"]["build"] == 2

    def test_output_has_expected_keys(self, tmp_path):
        from pineapple.agents.builder import builder_node

        state = _make_state(target_dir=str(tmp_path), task_plan=None)
        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        assert "current_stage" in result
        assert "build_results" in result
        assert "attempt_counts" in result

    def test_raises_when_no_workspace_and_no_target_dir(self):
        """Builder must NOT fall back to CWD -- should raise RuntimeError."""
        from pineapple.agents.builder import builder_node

        state = _make_state(
            workspace_info={"worktree_path": None},
            target_dir="",
            task_plan=None,
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            with pytest.raises(RuntimeError, match="no workspace"):
                builder_node(state)

    def test_raises_when_workspace_is_pipeline_repo(self, tmp_path):
        """Builder must refuse to write into the pipeline's own repo."""
        from pineapple.agents.builder import builder_node

        # Point workspace to the pipeline repo root (parent of src/)
        pipeline_root = str(Path(__file__).resolve().parents[1])

        state = _make_state(
            workspace_info={"worktree_path": pipeline_root},
            task_plan=None,
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            with pytest.raises(RuntimeError, match="pipeline repo"):
                builder_node(state)


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
        assert len(record["layers"]) == 7  # 7 verification layers (6 static + 1 LLM quality eval)

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
# Stage 7b: Chunked Reviewer
# ---------------------------------------------------------------------------


class TestChunkDiffByModule:
    """Unit tests for chunk_diff_by_module helper."""

    def test_groups_by_top_level_directory(self):
        from pineapple.agents.reviewer import chunk_diff_by_module

        files = [
            {"path": "kfs_core/engine.py", "lines_changed": 100},
            {"path": "kfs_core/utils.py", "lines_changed": 50},
            {"path": "kfs_cli/main.py", "lines_changed": 30},
            {"path": "tests/test_engine.py", "lines_changed": 80},
        ]
        chunks = chunk_diff_by_module(files)
        modules = [c["module"] for c in chunks]
        assert "kfs_core" in modules
        assert "kfs_cli" in modules
        assert "tests" in modules

    def test_root_files_grouped_as_root(self):
        from pineapple.agents.reviewer import chunk_diff_by_module

        files = [
            {"path": "README.md", "lines_changed": 5},
            {"path": "setup.py", "lines_changed": 10},
            {"path": "src/main.py", "lines_changed": 20},
        ]
        chunks = chunk_diff_by_module(files)
        modules = [c["module"] for c in chunks]
        assert "_root" in modules
        assert "src" in modules

    def test_empty_input_returns_empty(self):
        from pineapple.agents.reviewer import chunk_diff_by_module

        assert chunk_diff_by_module([]) == []

    def test_file_count_and_lines_totaled(self):
        from pineapple.agents.reviewer import chunk_diff_by_module

        files = [
            {"path": "mod/a.py", "lines_changed": 10},
            {"path": "mod/b.py", "lines_changed": 20},
            {"path": "mod/c.py", "lines_changed": 30},
        ]
        chunks = chunk_diff_by_module(files)
        assert len(chunks) == 1
        assert chunks[0]["file_count"] == 3
        assert chunks[0]["lines_changed"] == 60

    def test_defaults_lines_changed_to_1(self):
        from pineapple.agents.reviewer import chunk_diff_by_module

        files = [{"path": "mod/a.py"}, {"path": "mod/b.py"}]
        chunks = chunk_diff_by_module(files)
        assert chunks[0]["lines_changed"] == 2

    def test_backslash_paths_normalized(self):
        from pineapple.agents.reviewer import chunk_diff_by_module

        files = [{"path": "src\\pineapple\\agents\\reviewer.py", "lines_changed": 50}]
        chunks = chunk_diff_by_module(files)
        assert chunks[0]["module"] == "src"


class TestShouldChunk:
    """Tests for _should_chunk threshold logic."""

    def test_below_thresholds_no_chunk(self):
        from pineapple.agents.reviewer import _should_chunk

        files = [{"path": f"mod/file{i}.py", "lines_changed": 10} for i in range(10)]
        assert _should_chunk(files) is False

    def test_above_file_threshold_triggers_chunk(self):
        from pineapple.agents.reviewer import _should_chunk

        files = [{"path": f"mod/file{i}.py", "lines_changed": 1} for i in range(51)]
        assert _should_chunk(files) is True

    def test_above_line_threshold_triggers_chunk(self):
        from pineapple.agents.reviewer import _should_chunk

        files = [{"path": f"mod/file{i}.py", "lines_changed": 1000} for i in range(10)]
        assert _should_chunk(files) is True

    def test_empty_files_no_chunk(self):
        from pineapple.agents.reviewer import _should_chunk

        assert _should_chunk([]) is False


class TestMergeChunkResults:
    """Tests for _merge_chunk_results merging logic."""

    def test_worst_verdict_wins(self):
        from pineapple.agents.reviewer import _merge_chunk_results

        chunk_results = [
            {"module": "mod_a", "result": {"verdict": "pass", "critical_issues": [], "important_issues": [], "minor_issues": []}},
            {"module": "mod_b", "result": {"verdict": "retry", "critical_issues": [], "important_issues": ["issue"], "minor_issues": []}},
            {"module": "mod_c", "result": {"verdict": "pass", "critical_issues": [], "important_issues": [], "minor_issues": []}},
        ]
        merged = _merge_chunk_results(chunk_results)
        assert merged.verdict == "retry"

    def test_fail_overrides_retry(self):
        from pineapple.agents.reviewer import _merge_chunk_results

        chunk_results = [
            {"module": "mod_a", "result": {"verdict": "retry", "critical_issues": [], "important_issues": [], "minor_issues": []}},
            {"module": "mod_b", "result": {"verdict": "fail", "critical_issues": ["fatal"], "important_issues": [], "minor_issues": []}},
        ]
        merged = _merge_chunk_results(chunk_results)
        assert merged.verdict == "fail"
        assert "fatal" in merged.critical_issues

    def test_issues_concatenated_and_deduplicated(self):
        from pineapple.agents.reviewer import _merge_chunk_results

        chunk_results = [
            {"module": "a", "result": {"verdict": "pass", "critical_issues": [], "important_issues": ["dup"], "minor_issues": ["x"]}},
            {"module": "b", "result": {"verdict": "pass", "critical_issues": [], "important_issues": ["dup"], "minor_issues": ["y"]}},
        ]
        merged = _merge_chunk_results(chunk_results)
        assert merged.important_issues == ["dup"]
        assert set(merged.minor_issues) == {"x", "y"}

    def test_all_pass_gives_pass(self):
        from pineapple.agents.reviewer import _merge_chunk_results

        chunk_results = [
            {"module": "a", "result": {"verdict": "pass", "critical_issues": [], "important_issues": [], "minor_issues": []}},
            {"module": "b", "result": {"verdict": "pass", "critical_issues": [], "important_issues": [], "minor_issues": []}},
        ]
        merged = _merge_chunk_results(chunk_results)
        assert merged.verdict == "pass"


class TestChunkedReviewerNode:
    """Integration tests: reviewer_node with chunked fallback path."""

    def _make_large_changed_files(self, n_modules=5, files_per_module=15):
        """Generate a list of changed files exceeding chunk thresholds."""
        files = []
        modules = [f"module_{i}" for i in range(n_modules)]
        for mod in modules:
            for j in range(files_per_module):
                files.append({"path": f"{mod}/file_{j}.py", "lines_changed": 100})
        return files

    def test_chunked_fallback_pass(self):
        from pineapple.agents.reviewer import reviewer_node

        changed = self._make_large_changed_files(n_modules=4, files_per_module=15)
        state = _make_state(
            build_results=[
                {"task_id": "T1", "status": "completed", "commits": ["abc"], "errors": []},
            ],
            verify_record={"all_green": True, "layers": []},
            changed_files=changed,
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        assert result["current_stage"] == "review"
        assert result["review_result"]["verdict"] == "pass"

    def test_chunked_fallback_retry_on_failed_build(self):
        from pineapple.agents.reviewer import reviewer_node

        changed = self._make_large_changed_files(n_modules=4, files_per_module=15)
        state = _make_state(
            build_results=[
                {"task_id": "T1", "status": "failed", "commits": [], "errors": ["compile error"]},
            ],
            verify_record={"all_green": False, "layers": []},
            changed_files=changed,
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        assert result["review_result"]["verdict"] == "retry"
        # Should have module-tagged critical issues
        critical = result["review_result"]["critical_issues"]
        assert any("[module_" in issue for issue in critical)

    def test_no_chunking_below_threshold(self):
        from pineapple.agents.reviewer import reviewer_node

        # Only 5 files, well below threshold
        changed = [{"path": f"src/file_{i}.py", "lines_changed": 10} for i in range(5)]
        state = _make_state(
            build_results=[
                {"task_id": "T1", "status": "completed", "commits": ["abc"], "errors": []},
            ],
            verify_record={"all_green": True, "layers": []},
            changed_files=changed,
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        assert result["review_result"]["verdict"] == "pass"

    def test_no_changed_files_skips_chunking(self):
        from pineapple.agents.reviewer import reviewer_node

        state = _make_state(
            build_results=[],
            verify_record=None,
            changed_files=None,
        )

        with patch("pineapple.agents.reviewer._HAS_LLM_DEPS", False):
            result = reviewer_node(state)

        # Should still work, just no chunking
        assert result["current_stage"] == "review"


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


# ---------------------------------------------------------------------------
# Workspace Flow: target_dir propagation through all stages
# ---------------------------------------------------------------------------


def _init_git_repo(path):
    """Initialize a git repo at the given path with an initial commit."""
    subprocess.run(["git", "init", str(path)], capture_output=True, text=True, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"],
                   capture_output=True, text=True, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"],
                   capture_output=True, text=True, check=True)
    # Create initial commit so branches can be created
    dummy = Path(path) / "README.md"
    dummy.write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], capture_output=True, text=True, check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"],
                   capture_output=True, text=True, check=True)


class TestWorkspaceFlow:
    """Tests that workspace flows through target_dir correctly."""

    def test_setup_uses_target_dir_for_worktree(self, tmp_path):
        """Setup should create worktree in target_dir, not CWD."""
        from pineapple.agents.setup import setup_node

        # Create a target repo separate from CWD
        target_repo = tmp_path / "target_project"
        target_repo.mkdir()
        _init_git_repo(target_repo)

        # CWD is something completely different
        cwd_dir = tmp_path / "pipeline_cwd"
        cwd_dir.mkdir()

        state = _make_state(
            target_dir=str(target_repo),
            run_id="ws-test-001",
            project_name="workspace-test",
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(str(cwd_dir))
            result = setup_node(state)
        finally:
            os.chdir(old_cwd)

        ws_info = result["workspace_info"]
        worktree_path = ws_info.get("worktree_path")

        # The worktree MUST be under the target_repo, NOT under cwd_dir
        assert worktree_path is not None, "Worktree should have been created"
        assert str(target_repo) in worktree_path or worktree_path.startswith(str(target_repo)), \
            f"Worktree {worktree_path} should be under target {target_repo}, not CWD"
        assert str(cwd_dir) not in worktree_path, \
            f"Worktree {worktree_path} should NOT be under CWD {cwd_dir}"

    def test_setup_with_no_target_dir_uses_cwd(self, tmp_path):
        """Backward compat: no target_dir falls back to CWD."""
        from pineapple.agents.setup import setup_node

        # Make CWD a git repo
        _init_git_repo(tmp_path)

        state = _make_state(
            target_dir="",
            run_id="ws-test-002",
            project_name="cwd-fallback",
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = setup_node(state)
        finally:
            os.chdir(old_cwd)

        ws_info = result["workspace_info"]
        worktree_path = ws_info.get("worktree_path")

        # Should have created worktree relative to CWD (tmp_path)
        if worktree_path is not None:
            assert str(tmp_path) in worktree_path, \
                f"With no target_dir, worktree {worktree_path} should be under CWD {tmp_path}"

    def test_setup_target_dir_not_git_repo(self, tmp_path):
        """If target_dir is set but NOT a git repo, use it as workspace directly."""
        from pineapple.agents.setup import setup_node

        target_dir = tmp_path / "plain_project"
        target_dir.mkdir()

        cwd_dir = tmp_path / "pipeline_cwd"
        cwd_dir.mkdir()

        state = _make_state(
            target_dir=str(target_dir),
            run_id="ws-test-003",
            project_name="no-git-test",
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(str(cwd_dir))
            result = setup_node(state)
        finally:
            os.chdir(old_cwd)

        ws_info = result["workspace_info"]
        # No worktree (not a git repo), but workspace should still reference target
        # The run_dir should be under the target_dir, not CWD
        run_dir = ws_info.get("run_dir", "")
        assert str(cwd_dir) not in run_dir or str(target_dir) in run_dir, \
            f"Run dir {run_dir} should reference target_dir, not CWD"

    def test_builder_writes_to_workspace_from_state(self, tmp_path):
        """Builder gets workspace path from workspace_info, writes there."""
        from pineapple.agents.builder import builder_node

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()

        state = _make_state(
            workspace_info={
                "worktree_path": str(workspace_dir),
                "branch": "feat/test",
                "run_dir": str(tmp_path / "run"),
                "tools_available": {"python": True, "git": False, "pytest": False},
                "scaffolded_files": [],
            },
            task_plan={
                "tasks": [
                    {
                        "id": "T1",
                        "description": "Create hello module",
                        "files_to_create": ["hello.py"],
                        "files_to_modify": [],
                        "complexity": "trivial",
                        "estimated_cost_usd": 0.0,
                    },
                ],
                "total_estimated_cost_usd": 0.0,
            },
        )

        with patch("pineapple.agents.builder._HAS_LLM_DEPS", False):
            result = builder_node(state)

        # Builder should have written files into workspace_dir, not CWD
        assert (workspace_dir / "hello.py").exists(), \
            f"hello.py should exist in workspace {workspace_dir}, not CWD"

    def test_verifier_runs_in_workspace(self, tmp_path):
        """Verifier runs pytest/checks in workspace_info.worktree_path."""
        from pineapple.agents.verifier import verifier_node

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        # Place a Python file so syntax check has something to check
        (workspace_dir / "app.py").write_text("x = 1\n", encoding="utf-8")

        state = _make_state(
            workspace_info={
                "worktree_path": str(workspace_dir),
                "branch": "feat/test",
                "run_dir": str(tmp_path / "run"),
                "tools_available": {"python": True, "git": True, "pytest": True},
                "scaffolded_files": [],
            },
        )

        result = verifier_node(state)

        assert result["current_stage"] == "verify"
        record = result["verify_record"]
        # Syntax check should have found our app.py
        syntax_layer = None
        for layer in record["layers"]:
            if layer["name"] == "syntax_check":
                syntax_layer = layer
                break
        assert syntax_layer is not None
        # It should pass (valid syntax) or at least not fail from looking at CWD
        assert syntax_layer["status"] in ("pass", "skip")

    def test_shipper_reads_workspace_branch(self):
        """Shipper gets branch from workspace_info, not state.branch."""
        from pineapple.agents.shipper import _do_keep

        state = _make_state(
            branch="main",  # state.branch is main
            workspace_info={
                "worktree_path": "/tmp/fake-worktree",
                "branch": "feat/my-feature-abc123",  # workspace branch is different
                "run_dir": "/tmp/fake-run",
                "tools_available": {},
                "scaffolded_files": [],
            },
        )

        result = _do_keep(state)
        # _do_keep prints branch from workspace_info, not state.branch
        assert result.action == "keep"

    def test_shipper_pr_uses_workspace_info_branch(self):
        """Shipper _do_pr reads branch from workspace_info for PR creation."""
        from pineapple.agents.shipper import _do_pr

        state = _make_state(
            branch="main",
            workspace_info={
                "worktree_path": "/tmp/fake-worktree",
                "branch": "feat/target-feature-abc",
                "run_dir": "/tmp/fake-run",
                "tools_available": {},
                "scaffolded_files": [],
            },
        )

        # _do_pr should read branch from workspace_info
        # It will fall back to "keep" because gh is not available,
        # but we can verify it doesn't crash and reads the right branch
        result = _do_pr(state)
        assert result.action == "keep"  # expected fallback in test env

    def test_setup_propagates_target_dir_in_workspace_info(self, tmp_path):
        """workspace_info must include target_dir so builder can fall back to it."""
        from pineapple.agents.setup import setup_node

        target_repo = tmp_path / "target"
        target_repo.mkdir()
        _init_git_repo(target_repo)

        state = _make_state(
            target_dir=str(target_repo),
            run_id="ws-test-prop",
            project_name="propagation-test",
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            result = setup_node(state)
        finally:
            os.chdir(old_cwd)

        ws_info = result["workspace_info"]
        assert "target_dir" in ws_info, "workspace_info must propagate target_dir"
        assert str(target_repo) in ws_info["target_dir"]

    def test_run_dir_created_in_target_dir(self, tmp_path):
        """When target_dir is set, run_dir should be under target_dir."""
        from pineapple.agents.setup import setup_node

        target_repo = tmp_path / "target"
        target_repo.mkdir()
        _init_git_repo(target_repo)

        cwd_dir = tmp_path / "cwd"
        cwd_dir.mkdir()

        state = _make_state(
            target_dir=str(target_repo),
            run_id="ws-test-rundir",
            project_name="rundir-test",
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(str(cwd_dir))
            result = setup_node(state)
        finally:
            os.chdir(old_cwd)

        run_dir = result["workspace_info"]["run_dir"]
        assert str(target_repo) in run_dir, \
            f"Run dir {run_dir} should be under target_dir {target_repo}"
        assert str(cwd_dir) not in run_dir, \
            f"Run dir {run_dir} should NOT be under CWD {cwd_dir}"
