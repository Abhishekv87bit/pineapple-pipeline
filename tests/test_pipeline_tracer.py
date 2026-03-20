"""Tests for pipeline_tracer.py -- JSONL tracing for pipeline runs.

All tests use tmp_path fixtures and have no real filesystem dependencies.
"""

import json
import time

import pytest

from pipeline_tracer import PipelineTracer


# ---------------------------------------------------------------------------
# Trace file creation
# ---------------------------------------------------------------------------


class TestTraceFileCreation:
    def test_creates_trace_directory(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        assert tracer.trace_file.parent.is_dir()

    def test_trace_file_path(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        expected = tmp_path / ".pineapple" / "runs" / "run-001" / "trace.jsonl"
        assert tracer.trace_file == expected

    def test_empty_trace_file_not_created_until_write(self, tmp_path):
        """Trace file should not exist until something is written."""
        tracer = PipelineTracer(tmp_path, "run-001")
        assert not tracer.trace_file.is_file()


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class TestLogStageTransition:
    def test_basic_transition(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("INTAKE", "BRAINSTORM", reason="ready")

        entries = tracer.get_trace()
        assert len(entries) == 1
        assert entries[0]["event"] == "stage_transition"
        assert entries[0]["from_stage"] == "INTAKE"
        assert entries[0]["to_stage"] == "BRAINSTORM"
        assert entries[0]["reason"] == "ready"
        assert entries[0]["run_id"] == "run-001"
        assert "timestamp" in entries[0]

    def test_transition_with_duration(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("BUILD", "VERIFY", duration_ms=1234.567)

        entries = tracer.get_trace()
        assert entries[0]["duration_ms"] == 1234.6

    def test_transition_with_metadata(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("A", "B", metadata={"key": "value"})

        entries = tracer.get_trace()
        assert entries[0]["metadata"] == {"key": "value"}


class TestLogAgentDispatch:
    def test_dispatch_event(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_agent_dispatch("BUILD", "coder", "implement feature X")

        entries = tracer.get_trace()
        assert len(entries) == 1
        assert entries[0]["event"] == "agent_dispatch"
        assert entries[0]["stage"] == "BUILD"
        assert entries[0]["agent_type"] == "coder"
        assert entries[0]["task"] == "implement feature X"


class TestLogAgentResult:
    def test_success_result(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_agent_result("BUILD", "coder", success=True, duration_ms=5000.0)

        entries = tracer.get_trace()
        assert entries[0]["event"] == "agent_result"
        assert entries[0]["success"] is True
        assert entries[0]["duration_ms"] == 5000.0
        assert entries[0]["error"] is None

    def test_failure_result(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_agent_result("BUILD", "coder", success=False, error="compilation failed")

        entries = tracer.get_trace()
        assert entries[0]["success"] is False
        assert entries[0]["error"] == "compilation failed"


class TestLogVerification:
    def test_verification_event(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_verification(
            layers_passed=[1, 2, 3],
            layers_failed=[4],
            test_count=42,
            all_green=False,
            duration_ms=3000.0,
        )

        entries = tracer.get_trace()
        assert entries[0]["event"] == "verification"
        assert entries[0]["layers_passed"] == [1, 2, 3]
        assert entries[0]["layers_failed"] == [4]
        assert entries[0]["test_count"] == 42
        assert entries[0]["all_green"] is False
        assert entries[0]["duration_ms"] == 3000.0


class TestLogCost:
    def test_cost_event(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_cost(
            model="claude-opus-4",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.123456,
            call_name="brainstorm",
        )

        entries = tracer.get_trace()
        assert entries[0]["event"] == "llm_cost"
        assert entries[0]["model"] == "claude-opus-4"
        assert entries[0]["input_tokens"] == 1000
        assert entries[0]["output_tokens"] == 500
        assert entries[0]["cost_usd"] == 0.123456
        assert entries[0]["call_name"] == "brainstorm"


class TestLogError:
    def test_recoverable_error(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_error("BUILD", "timeout on test", recoverable=True)

        entries = tracer.get_trace()
        assert entries[0]["event"] == "error"
        assert entries[0]["stage"] == "BUILD"
        assert entries[0]["error"] == "timeout on test"
        assert entries[0]["recoverable"] is True

    def test_fatal_error(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_error("VERIFY", "disk full", recoverable=False)

        entries = tracer.get_trace()
        assert entries[0]["recoverable"] is False


class TestLogCustom:
    def test_custom_event(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_custom("user_input", prompt="What should we build?", response="A widget")

        entries = tracer.get_trace()
        assert entries[0]["event"] == "user_input"
        assert entries[0]["prompt"] == "What should we build?"
        assert entries[0]["response"] == "A widget"


# ---------------------------------------------------------------------------
# trace_stage context manager
# ---------------------------------------------------------------------------


class TestTraceStage:
    def test_records_transition_on_success(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        with tracer.trace_stage("INTAKE", "BRAINSTORM", reason="starting"):
            pass  # simulate work

        entries = tracer.get_trace()
        assert len(entries) == 1
        assert entries[0]["event"] == "stage_transition"
        assert entries[0]["from_stage"] == "INTAKE"
        assert entries[0]["to_stage"] == "BRAINSTORM"
        assert entries[0]["duration_ms"] >= 0

    def test_records_duration(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        with tracer.trace_stage("A", "B"):
            time.sleep(0.05)

        entries = tracer.get_trace()
        # Duration should be at least 50ms
        assert entries[0]["duration_ms"] >= 40  # allow slight timing variance

    def test_records_error_on_exception(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        with pytest.raises(ValueError, match="boom"):
            with tracer.trace_stage("BUILD", "VERIFY"):
                raise ValueError("boom")

        entries = tracer.get_trace()
        assert len(entries) == 1
        assert entries[0]["event"] == "error"
        assert entries[0]["stage"] == "BUILD"
        assert "boom" in entries[0]["error"]
        assert entries[0]["recoverable"] is False


# ---------------------------------------------------------------------------
# get_trace
# ---------------------------------------------------------------------------


class TestGetTrace:
    def test_empty_trace(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        assert tracer.get_trace() == []

    def test_returns_all_events(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("A", "B")
        tracer.log_error("B", "oops")
        tracer.log_cost("model", 100, 50, 0.01)

        entries = tracer.get_trace()
        assert len(entries) == 3
        events = [e["event"] for e in entries]
        assert events == ["stage_transition", "error", "llm_cost"]


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


class TestGetSummary:
    def test_empty_summary(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        summary = tracer.get_summary()
        assert summary["total_events"] == 0

    def test_summary_totals(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("INTAKE", "BRAINSTORM")
        tracer.log_stage_transition("BRAINSTORM", "PLAN")
        tracer.log_cost("claude", 1000, 500, 0.10)
        tracer.log_cost("claude", 2000, 800, 0.25)
        tracer.log_error("BUILD", "oops")

        summary = tracer.get_summary()
        assert summary["total_events"] == 5
        assert summary["stage_transitions"] == 2
        assert summary["llm_calls"] == 2
        assert summary["total_cost_usd"] == 0.35
        assert summary["total_tokens"] == 4300  # (1000+500) + (2000+800)
        assert summary["errors"] == 1

    def test_summary_timestamps(self, tmp_path):
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("A", "B")
        time.sleep(0.01)
        tracer.log_stage_transition("B", "C")

        summary = tracer.get_summary()
        assert summary["first_event"] != ""
        assert summary["last_event"] != ""
        assert summary["first_event"] <= summary["last_event"]


# ---------------------------------------------------------------------------
# Append-only behavior
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def test_multiple_writes_accumulate(self, tmp_path):
        """Multiple log calls should append, not overwrite."""
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("A", "B")
        tracer.log_stage_transition("B", "C")
        tracer.log_stage_transition("C", "D")

        entries = tracer.get_trace()
        assert len(entries) == 3
        assert entries[0]["from_stage"] == "A"
        assert entries[1]["from_stage"] == "B"
        assert entries[2]["from_stage"] == "C"

    def test_jsonl_format_one_object_per_line(self, tmp_path):
        """Each entry should be a single valid JSON line."""
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("A", "B")
        tracer.log_error("B", "problem")

        lines = tracer.trace_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert "event" in parsed
            assert "run_id" in parsed
            assert "timestamp" in parsed

    def test_all_entries_have_run_id_and_timestamp(self, tmp_path):
        """Every entry must include run_id and timestamp."""
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_stage_transition("A", "B")
        tracer.log_agent_dispatch("B", "coder", "task")
        tracer.log_cost("model", 100, 50, 0.01)
        tracer.log_custom("custom_event", data="hello")

        for entry in tracer.get_trace():
            assert entry["run_id"] == "run-001"
            assert len(entry["timestamp"]) > 10  # ISO format


# ---------------------------------------------------------------------------
# check_cost_ceiling
# ---------------------------------------------------------------------------


class TestCheckCostCeiling:
    def test_no_costs_returns_continue(self, tmp_path):
        """With no costs logged, result should be continue with total_cost=0."""
        tracer = PipelineTracer(tmp_path, "run-001")
        result = tracer.check_cost_ceiling(ceiling_usd=200.0)

        assert result["exceeded"] is False
        assert result["total_cost"] == 0.0
        assert result["ceiling"] == 200.0
        assert result["remaining"] == 200.0
        assert result["recommendation"] == "continue"

    def test_costs_under_80_percent_returns_continue(self, tmp_path):
        """Costs below 80% of ceiling should return 'continue'."""
        tracer = PipelineTracer(tmp_path, "run-001")
        # Log $100 against a $200 ceiling — 50%, under the 80% threshold
        tracer.log_cost("claude", 1000, 500, 100.0)
        result = tracer.check_cost_ceiling(ceiling_usd=200.0)

        assert result["exceeded"] is False
        assert result["total_cost"] == 100.0
        assert result["remaining"] == 100.0
        assert result["recommendation"] == "continue"

    def test_costs_just_above_80_percent_returns_warning(self, tmp_path):
        """Costs just above 80% of ceiling should return 'warning'.

        The boundary is strict (cost > ceiling * 0.8), so exactly 80% still
        returns 'continue'.  This test uses $161 (80.5%) to exercise the
        warning band.
        """
        tracer = PipelineTracer(tmp_path, "run-001")
        # Log $161 against a $200 ceiling — 80.5%, strictly above the threshold
        tracer.log_cost("claude", 1000, 500, 161.0)
        result = tracer.check_cost_ceiling(ceiling_usd=200.0)

        assert result["exceeded"] is False
        assert result["recommendation"] == "warning"

    def test_costs_above_80_percent_returns_warning(self, tmp_path):
        """Costs between 80% and 100% of ceiling should return 'warning'."""
        tracer = PipelineTracer(tmp_path, "run-001")
        # Log $190 against a $200 ceiling — 95%
        tracer.log_cost("claude", 1000, 500, 190.0)
        result = tracer.check_cost_ceiling(ceiling_usd=200.0)

        assert result["exceeded"] is False
        assert result["total_cost"] == 190.0
        assert result["recommendation"] == "warning"

    def test_costs_over_ceiling_returns_exceeded(self, tmp_path):
        """Costs exceeding the ceiling should set exceeded=True and return 'exceeded'."""
        tracer = PipelineTracer(tmp_path, "run-001")
        # Log $250 against a $200 ceiling
        tracer.log_cost("claude", 1000, 500, 250.0)
        result = tracer.check_cost_ceiling(ceiling_usd=200.0)

        assert result["exceeded"] is True
        assert result["total_cost"] == 250.0
        assert result["remaining"] == -50.0
        assert result["recommendation"] == "exceeded"

    def test_ceiling_and_remaining_fields_present(self, tmp_path):
        """Result dict must always include all five keys."""
        tracer = PipelineTracer(tmp_path, "run-001")
        result = tracer.check_cost_ceiling()

        assert set(result.keys()) == {"exceeded", "total_cost", "ceiling", "remaining", "recommendation"}

    def test_custom_ceiling_value(self, tmp_path):
        """ceiling_usd parameter should be honoured."""
        tracer = PipelineTracer(tmp_path, "run-001")
        tracer.log_cost("claude", 100, 50, 5.0)
        # $5 against a $10 ceiling — 50%, should continue
        result = tracer.check_cost_ceiling(ceiling_usd=10.0)
        assert result["recommendation"] == "continue"
        assert result["ceiling"] == 10.0

        # $5 against a $5 ceiling — exactly at limit; 5 > 5 is False so not exceeded,
        # but 5 > 5*0.8=4 is True, so warning
        result2 = tracer.check_cost_ceiling(ceiling_usd=5.0)
        assert result2["recommendation"] == "warning"
        assert result2["exceeded"] is False
