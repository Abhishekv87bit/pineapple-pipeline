"""Tests for Layer 7: DeepEval LLM quality evaluation in verifier.py."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a minimal fake deepeval module tree so we can control imports
# ---------------------------------------------------------------------------

def _make_deepeval_mocks(geval_score=0.85, faithfulness_score=0.85):
    """Return a dict of mock objects for deepeval imports."""
    fake_geval = MagicMock()
    fake_geval.score = geval_score

    fake_faithfulness = MagicMock()
    fake_faithfulness.score = faithfulness_score

    GEvalClass = MagicMock(return_value=fake_geval)
    FaithfulnessMetricClass = MagicMock(return_value=fake_faithfulness)

    LLMTestCaseClass = MagicMock()
    LLMTestCaseParamsClass = MagicMock()
    LLMTestCaseParamsClass.INPUT = "INPUT"
    LLMTestCaseParamsClass.ACTUAL_OUTPUT = "ACTUAL_OUTPUT"
    LLMTestCaseParamsClass.EXPECTED_OUTPUT = "EXPECTED_OUTPUT"

    return {
        "GEvalClass": GEvalClass,
        "FaithfulnessMetricClass": FaithfulnessMetricClass,
        "LLMTestCaseClass": LLMTestCaseClass,
        "LLMTestCaseParamsClass": LLMTestCaseParamsClass,
        "geval_instance": fake_geval,
        "faithfulness_instance": fake_faithfulness,
    }


def _patch_deepeval(mocks):
    """Return a context manager that injects fake deepeval modules."""
    fake_metrics = ModuleType("deepeval.metrics")
    fake_metrics.GEval = mocks["GEvalClass"]
    fake_metrics.FaithfulnessMetric = mocks["FaithfulnessMetricClass"]

    fake_test_case = ModuleType("deepeval.test_case")
    fake_test_case.LLMTestCase = mocks["LLMTestCaseClass"]
    fake_test_case.LLMTestCaseParams = mocks["LLMTestCaseParamsClass"]

    fake_deepeval = ModuleType("deepeval")

    patch_sys = patch.dict(
        "sys.modules",
        {
            "deepeval": fake_deepeval,
            "deepeval.metrics": fake_metrics,
            "deepeval.test_case": fake_test_case,
        },
    )
    return patch_sys


# ---------------------------------------------------------------------------
# Import the function under test (after helpers defined)
# ---------------------------------------------------------------------------

from pineapple.agents.verifier import _run_deepeval  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeepEvalLayerSkipConditions:
    def test_skip_when_deepeval_not_installed(self):
        """Layer 7 must gracefully skip when deepeval is not importable."""
        with patch.dict("sys.modules", {"deepeval": None, "deepeval.metrics": None, "deepeval.test_case": None}):
            result = _run_deepeval(
                build_results=[{"files_written": ["foo.py"]}],
                design_spec={"summary": "Build a hello world function"},
            )
        assert result.layer == 7
        assert result.name == "deepeval_quality"
        assert result.status == "skip"
        assert "deepeval not installed" in result.details

    def test_skip_when_no_build_results(self):
        """Layer 7 must skip when build_results is empty/None."""
        mocks = _make_deepeval_mocks()
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=None,
                design_spec={"summary": "Build something"},
            )
        assert result.status == "skip"
        assert "No build results" in result.details

    def test_skip_when_build_results_empty_list(self):
        """Layer 7 must skip when build_results is an empty list."""
        mocks = _make_deepeval_mocks()
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[],
                design_spec={"summary": "Build something"},
            )
        assert result.status == "skip"

    def test_skip_when_no_design_spec(self):
        """Layer 7 must skip when design_spec is None."""
        mocks = _make_deepeval_mocks()
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["foo.py"]}],
                design_spec=None,
            )
        assert result.status == "skip"
        assert "No build results or design spec" in result.details

    def test_skip_when_empty_code_output(self):
        """Layer 7 must skip when all build result entries have empty files_written."""
        mocks = _make_deepeval_mocks()
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": []}],
                design_spec={"summary": "Build something"},
            )
        assert result.status == "skip"
        assert "No code output" in result.details


class TestDeepEvalLayerPassConditions:
    def test_pass_when_scores_above_threshold(self):
        """Layer 7 must pass when both GEval and Faithfulness return >= 0.7."""
        mocks = _make_deepeval_mocks(geval_score=0.85, faithfulness_score=0.90)
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["module.py", "utils.py"]}],
                design_spec={"summary": "Implement a REST API endpoint for user login"},
            )
        assert result.status == "pass"
        assert "All quality gates passed" in result.details
        assert "geval=0.85" in result.details
        assert "faithfulness=0.90" in result.details

    def test_pass_scores_at_exact_threshold(self):
        """Layer 7 must pass when scores are exactly at the 0.7 threshold."""
        mocks = _make_deepeval_mocks(geval_score=0.70, faithfulness_score=0.70)
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["app.py"]}],
                design_spec={"summary": "Simple CLI tool"},
            )
        # 0.70 is NOT < 0.7, so should pass
        assert result.status == "pass"


class TestDeepEvalLayerFailConditions:
    def test_fail_when_geval_below_threshold(self):
        """Layer 7 must fail when GEval score is below 0.7."""
        mocks = _make_deepeval_mocks(geval_score=0.50, faithfulness_score=0.85)
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["output.py"]}],
                design_spec={"summary": "Implement a sorting algorithm"},
            )
        assert result.status == "fail"
        assert "GEval score 0.50 < 0.7 threshold" in result.details

    def test_fail_when_faithfulness_below_threshold(self):
        """Layer 7 must fail when Faithfulness score is below 0.7."""
        mocks = _make_deepeval_mocks(geval_score=0.85, faithfulness_score=0.40)
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["output.py"]}],
                design_spec={"summary": "Implement a sorting algorithm"},
            )
        assert result.status == "fail"
        assert "Faithfulness score 0.40 < 0.7 threshold" in result.details

    def test_fail_when_both_below_threshold(self):
        """Layer 7 must fail and report both failures when both metrics are low."""
        mocks = _make_deepeval_mocks(geval_score=0.30, faithfulness_score=0.20)
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["main.py"]}],
                design_spec={"summary": "Build a database ORM"},
            )
        assert result.status == "fail"
        assert result.fail_count == 2


class TestDeepEvalLayerResultFields:
    def test_layer_result_fields_correct_on_pass(self):
        """LayerResult fields must be correct on a passing run."""
        mocks = _make_deepeval_mocks(geval_score=0.80, faithfulness_score=0.90)
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["api.py"]}],
                design_spec={"summary": "Build an API"},
            )
        assert result.layer == 7
        assert result.name == "deepeval_quality"
        assert result.status == "pass"
        assert result.test_count == 2  # geval + faithfulness
        assert result.fail_count == 0

    def test_layer_result_fields_correct_on_fail(self):
        """LayerResult fields must be correct on a failing run."""
        mocks = _make_deepeval_mocks(geval_score=0.50, faithfulness_score=0.60)
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["api.py"]}],
                design_spec={"summary": "Build an API"},
            )
        assert result.layer == 7
        assert result.name == "deepeval_quality"
        assert result.status == "fail"
        assert result.test_count == 2
        assert result.fail_count == 2

    def test_layer_result_fields_correct_on_skip(self):
        """LayerResult fields must be correct on a skip."""
        mocks = _make_deepeval_mocks()
        with _patch_deepeval(mocks):
            result = _run_deepeval(build_results=None, design_spec=None)
        assert result.layer == 7
        assert result.name == "deepeval_quality"
        assert result.status == "skip"

    def test_metric_exception_counts_as_failure(self):
        """If a metric raises during measure(), it should be counted as a failure."""
        mocks = _make_deepeval_mocks()
        mocks["geval_instance"].measure.side_effect = RuntimeError("LLM API error")
        with _patch_deepeval(mocks):
            result = _run_deepeval(
                build_results=[{"files_written": ["code.py"]}],
                design_spec={"summary": "Do something"},
            )
        # GEval failed with exception — should show in details and count as failure
        assert "GEval failed" in result.details
        assert result.status == "fail"
