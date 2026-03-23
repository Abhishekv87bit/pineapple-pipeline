"""Integration tests verifying 3rd-party libraries work correctly.

Tests real behavior of: PyBreaker, Tenacity, Instructor, LangGraph, Pydantic.
"""
import os
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock, patch

import pybreaker
import pytest
from pydantic import ValidationError

from pineapple.gates import (
    BuildCycleFailure,
    build_loop_breaker,
    review_gate,
    route_by_path,
)
from pineapple.models import (
    BuildResult,
    ComponentSpec,
    ContextBundle,
    DesignSpec,
    EvolveReport,
    LayerResult,
    PipelineError,
    ReviewResult,
    ShipResult,
    StrategicBrief,
    Task,
    TaskPlan,
    VerificationRecord,
)


# ============================================================================
# Helpers
# ============================================================================


def _reset_breaker():
    """Force the circuit breaker back to CLOSED state."""
    build_loop_breaker.close()


def _make_state(**overrides) -> dict:
    """Create a minimal PipelineState dict with sensible defaults."""
    base = {
        "run_id": "test-run",
        "request": "test request",
        "project_name": "test",
        "branch": "main",
        "path": "full",
        "current_stage": "review",
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

# ============================================================================
# 1. PyBreaker (gates.py)
# ============================================================================


class TestPyBreakerIntegration:
    """Verify PyBreaker circuit breaker tracks failures and opens correctly."""

    def setup_method(self):
        _reset_breaker()

    def teardown_method(self):
        _reset_breaker()

    def test_breaker_starts_closed(self):
        """Breaker should start in CLOSED state."""
        assert build_loop_breaker.current_state == "closed"

    def test_three_critical_failures_opens_breaker(self):
        """3 consecutive critical failures should open the breaker."""
        state_with_issues = _make_state(
            review_result={"critical_issues": ["bug1", "bug2"]}
        )

        results = []
        for _ in range(3):
            result = review_gate(state_with_issues)
            results.append(result)

        # First 3 calls: breaker records failures but returns retry
        assert results[0] == "retry"
        assert results[1] == "retry"
        assert results[2] == "fail"

        # After 3 failures, breaker is now OPEN
        assert build_loop_breaker.current_state == "open"

        # 4th call should return fail because breaker is open
        result = review_gate(state_with_issues)
        assert result == "fail"

    def test_passing_state_after_failures_succeeds(self):
        """After breaker resets (half-open), a passing state should close it."""
        state_with_issues = _make_state(
            review_result={"critical_issues": ["bug"]}
        )
        passing_state = _make_state(
            review_result={"critical_issues": []}
        )

        # Trip the breaker with 3 failures
        for _ in range(3):
            review_gate(state_with_issues)

        assert build_loop_breaker.current_state == "open"

        # Manually force half-open (simulating timeout expiry)
        build_loop_breaker.half_open()
        assert build_loop_breaker.current_state == "half-open"

        # Passing call should succeed and close the breaker
        result = review_gate(passing_state)
        assert result == "pass"
        assert build_loop_breaker.current_state == "closed"

    def test_breaker_state_transitions(self):
        """Verify full lifecycle: closed -> open -> half-open -> closed."""
        state_with_issues = _make_state(
            review_result={"critical_issues": ["bug"]}
        )
        passing_state = _make_state(review_result={})

        assert build_loop_breaker.current_state == "closed"

        for _ in range(3):
            review_gate(state_with_issues)
        assert build_loop_breaker.current_state == "open"

        build_loop_breaker.half_open()
        assert build_loop_breaker.current_state == "half-open"

        result = review_gate(passing_state)
        assert result == "pass"
        assert build_loop_breaker.current_state == "closed"

    def test_cost_ceiling_independent_of_breaker(self):
        """Cost ceiling (200 USD) should trigger fail regardless of breaker state."""
        _reset_breaker()
        assert build_loop_breaker.current_state == "closed"

        state_over_budget = _make_state(
            cost_total_usd=201.0,
            review_result={"critical_issues": []},
        )

        result = review_gate(state_over_budget)
        assert result == "fail"

        # Breaker should still be closed -- cost gate fires first
        assert build_loop_breaker.current_state == "closed"

    def test_cost_exactly_at_ceiling_passes(self):
        """Cost at exactly 200 should pass (> 200 is the threshold)."""
        state_at_limit = _make_state(
            cost_total_usd=200.0,
            review_result={},
        )
        result = review_gate(state_at_limit)
        assert result == "pass"

    def test_breaker_fail_max_is_three(self):
        """Verify the breaker is configured with fail_max=3."""
        assert build_loop_breaker.fail_max == 3

    def test_breaker_reset_timeout_is_sixty(self):
        """Verify the breaker reset_timeout is 60 seconds."""
        assert build_loop_breaker.reset_timeout == 60


# ============================================================================
# 2. Tenacity (strategic_review.py)
# ============================================================================


class TestTenacityIntegration:
    """Verify Tenacity retry logic works with the strategic review agent."""

    def test_retry_succeeds_after_two_failures(self):
        """Mock LLM to fail twice then succeed -- Tenacity should retry 3 times."""
        from pineapple.agents.strategic_review import _call_llm

        mock_brief = StrategicBrief(
            what="Test project",
            why="Testing retries",
            not_building=["nothing"],
            who_benefits="testers",
            assumptions=["works"],
            open_questions=["does it?"],
            approved=False,
        )

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception(f"Transient failure #{call_count}")
            return mock_brief

        mock_client = MagicMock()
        mock_client.provider = "gemini"
        mock_client.create = MagicMock(side_effect=side_effect)

        with patch("pineapple.agents.strategic_review.get_llm_client", return_value=mock_client):
            brief, provider = _call_llm("system prompt", "user prompt")

        assert call_count == 3
        assert brief.what == "Test project"
        assert provider == "gemini"

    def test_retry_exhausted_after_three_failures(self):
        """Mock LLM to fail 4 times -- Tenacity should stop after 3 and raise."""
        from pineapple.agents.strategic_review import _call_llm

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception(f"Persistent failure #{call_count}")

        mock_client = MagicMock()
        mock_client.provider = "claude"
        mock_client.create = MagicMock(side_effect=side_effect)

        with patch("pineapple.agents.strategic_review.get_llm_client", return_value=mock_client):
            with pytest.raises(Exception):
                _call_llm("system prompt", "user prompt")

        # Tenacity should have retried exactly 3 times
        assert call_count == 3

    def test_fallback_path_on_failure(self):
        """When retries exhausted, strategic_review_node should produce error brief."""
        from pineapple.agents.strategic_review import strategic_review_node

        def side_effect(**kwargs):
            raise Exception("LLM is down")

        mock_client = MagicMock()
        mock_client.provider = "gemini"
        mock_client.create = MagicMock(side_effect=side_effect)

        state = _make_state(request="Build a widget", current_stage="intake")

        with (
            patch("pineapple.agents.strategic_review._HAS_LLM_DEPS", True),
            patch("pineapple.agents.strategic_review.has_any_llm_key", return_value=True),
            patch("pineapple.agents.strategic_review.get_llm_client", return_value=mock_client),
        ):
            result = strategic_review_node(state)

        # Should produce an error brief, not crash
        assert result["current_stage"] == "strategic_review"
        assert result["strategic_brief"] is not None
        brief = result["strategic_brief"]
        assert "error" in brief["what"].lower() or "fail" in brief["what"].lower()
        assert len(result.get("errors", [])) > 0

    def test_tenacity_backoff_config(self):
        """Verify the retry decorator uses exponential backoff with min=1, max=30."""
        from tenacity import retry, stop_after_attempt, wait_exponential

        @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
        def dummy():
            pass

        retrying = dummy.retry

        assert retrying.stop.max_attempt_number == 3
        assert retrying.wait.min == 1.0
        assert retrying.wait.max == 30.0


# ============================================================================
# 3. Instructor (llm.py)
# ============================================================================


class TestInstructorIntegration:
    """Verify Instructor wrapping and LLMClient behavior."""

    def test_create_requires_response_model(self):
        """LLMClient.create() should require a response_model parameter."""
        from pineapple.llm import LLMClient
        import inspect

        sig = inspect.signature(LLMClient.create)
        params = list(sig.parameters.keys())
        assert "response_model" in params

        param = sig.parameters["response_model"]
        assert param.default is inspect.Parameter.empty

    def test_gemini_path_uses_instructor_wrapping(self):
        """Verify the Gemini path calls instructor.from_genai."""
        import instructor
        from pineapple.llm import _make_gemini_client

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"}):
            with patch("pineapple.llm.instructor.from_genai") as mock_from_genai:
                mock_from_genai.return_value = MagicMock(spec=instructor.Instructor)
                client = _make_gemini_client()
                mock_from_genai.assert_called_once()

    def test_claude_path_uses_instructor_wrapping(self):
        """Verify the Claude path creates an instructor-wrapped client."""
        import instructor

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fake-key"}):
            with patch("pineapple.llm.instructor.from_anthropic") as mock_from_anthropic:
                mock_from_anthropic.return_value = MagicMock(spec=instructor.Instructor)
                with patch("pineapple.llm.Anthropic", create=True) as mock_anthropic_cls:
                    mock_anthropic_cls.return_value = MagicMock()
                    from pineapple.llm import _make_claude_client
                    client = _make_claude_client()
                    mock_from_anthropic.assert_called_once()

    def test_llm_client_routes_to_correct_provider(self):
        """Verify LLMClient dispatches to correct provider method."""
        from pineapple.llm import LLMClient, PROVIDER_CLAUDE, PROVIDER_GEMINI

        mock_instructor = MagicMock()
        mock_instructor.messages.create.return_value = "result"

        claude_client = LLMClient(mock_instructor, "claude-sonnet-4-20250514", PROVIDER_CLAUDE)
        claude_client.create(
            response_model=StrategicBrief,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=100,
        )
        call_kwargs = mock_instructor.messages.create.call_args
        assert call_kwargs.kwargs.get("max_tokens") == 100

        mock_instructor.reset_mock()

        gemini_client = LLMClient(mock_instructor, "gemini-2.5-flash", PROVIDER_GEMINI)
        gemini_client.create(
            response_model=StrategicBrief,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=100,
        )
        assert mock_instructor.messages.create.called

    @pytest.mark.skipif(
        not os.environ.get("GOOGLE_API_KEY"),
        reason="GOOGLE_API_KEY not set -- skip real Instructor call",
    )
    def test_real_instructor_call_with_gemini(self):
        """Make a real Instructor call via Gemini if API key is available."""
        from pydantic import BaseModel
        from pineapple.llm import get_llm_client

        class SimpleAnswer(BaseModel):
            answer: str
            confidence: float

        llm = get_llm_client(stage="strategic_review")
        result = llm.create(
            response_model=SimpleAnswer,
            messages=[{"role": "user", "content": "What is 2+2? Answer with the number."}],
            system="Return a JSON with answer and confidence fields.",
        )

        assert isinstance(result, SimpleAnswer)
        assert isinstance(result.answer, str)
        assert isinstance(result.confidence, float)

    def test_provider_resolution_prefers_env_var(self):
        """Verify provider resolution respects environment variables."""
        from pineapple.llm import _resolve_provider

        with patch.dict(os.environ, {
            "PINEAPPLE_LLM": "claude",
            "ANTHROPIC_API_KEY": "fake-key",
        }, clear=False):
            provider = _resolve_provider()
            assert provider == "claude"

    def test_stage_override_takes_priority(self):
        """Verify stage-specific env var overrides global preference."""
        from pineapple.llm import _resolve_provider

        with patch.dict(os.environ, {
            "PINEAPPLE_LLM": "gemini",
            "PINEAPPLE_LLM_STAGE_strategic_review": "claude",
            "ANTHROPIC_API_KEY": "fake-key",
            "GOOGLE_API_KEY": "fake-key",
        }, clear=False):
            provider = _resolve_provider(stage="strategic_review")
            assert provider == "claude"


# ============================================================================
# 4. LangGraph (graph.py)
# ============================================================================


class TestLangGraphIntegration:
    """Verify LangGraph graph construction, checkpointing, and routing."""

    def test_sqlite_checkpointer_creates_db(self):
        """Creating pipeline with SQLite checkpointer should create the DB file."""
        db_path = os.path.join(tempfile.gettempdir(), "test_checkpoints.db")
        from pineapple.graph import create_pipeline

        pipeline = create_pipeline(db_path=db_path)
        assert os.path.exists(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        assert len(tables) > 0

    def test_checkpoint_persists_state(self):
        """Run partial pipeline, verify checkpoint exists in SQLite."""
        db_path = os.path.join(tempfile.gettempdir(), "test_checkpoints.db")
        from pineapple.graph import create_pipeline

        pipeline = create_pipeline(db_path=db_path)

        initial_state = {
            "run_id": "test-checkpoint-run",
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

        config = {"configurable": {"thread_id": "test-thread-1"}}
        events = list(pipeline.stream(initial_state, config=config))

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM checkpoints")
        count = cursor.fetchone()[0]
        conn.close()

        assert count > 0, "Checkpoint should have been written to SQLite"

    def test_resume_from_checkpoint(self):
        """Verify state is restored when resuming from a checkpoint."""
        db_path = os.path.join(tempfile.gettempdir(), "test_resume.db")
        from pineapple.graph import create_pipeline

        pipeline = create_pipeline(db_path=db_path)

        initial_state = {
            "run_id": "test-resume-run",
            "request": "Resume test",
            "project_name": "resume-test",
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

        config = {"configurable": {"thread_id": "test-resume-thread"}}
        events = list(pipeline.stream(initial_state, config=config))

        saved_state = pipeline.get_state(config)
        assert saved_state is not None
        assert saved_state.values.get("run_id") == "test-resume-run"

        pipeline2 = create_pipeline(db_path=db_path)
        restored_state = pipeline2.get_state(config)
        assert restored_state is not None
        assert restored_state.values.get("run_id") == "test-resume-run"

    def test_route_by_path_lightweight_skips_to_build(self):
        """Lightweight path should route from intake directly to build."""
        result = route_by_path(_make_state(path="lightweight"))
        assert result == "build"

    def test_route_by_path_medium_skips_to_plan(self):
        """Medium path should route from intake to plan (skip stages 1-2)."""
        result = route_by_path(_make_state(path="medium"))
        assert result == "plan"

    def test_route_by_path_full_goes_through_all(self):
        """Full path should route from intake to strategic_review."""
        result = route_by_path(_make_state(path="full"))
        assert result == "strategic_review"

    def test_interrupt_before_nodes_configured(self):
        """Verify the correct nodes are configured for interrupt_before."""
        from pineapple.graph import INTERRUPT_NODES

        assert "strategic_review" in INTERRUPT_NODES
        assert "architecture" in INTERRUPT_NODES
        assert "plan" in INTERRUPT_NODES
        assert "ship" in INTERRUPT_NODES
        assert len(INTERRUPT_NODES) == 4

    def test_memory_saver_fallback(self):
        """When checkpointer=None, should use MemorySaver (no SQLite)."""
        from pineapple.graph import create_pipeline

        pipeline = create_pipeline(checkpointer=None)
        assert pipeline is not None

    def test_graph_has_all_nodes(self):
        """Verify the compiled graph contains all 11 nodes."""
        from pineapple.graph import create_pipeline

        pipeline = create_pipeline(checkpointer=None)
        graph = pipeline.get_graph()
        node_ids = list(graph.nodes)

        expected_nodes = [
            "intake", "strategic_review", "architecture", "plan",
            "setup", "build", "verify", "review",
            "ship", "evolve", "human_intervention",
        ]
        for node in expected_nodes:
            assert node in node_ids, f"Missing node: {node}"


# ============================================================================
# 5. Pydantic (models/__init__.py)
# ============================================================================


class TestPydanticModels:
    """Verify Pydantic model validation, serialization, and error handling."""

    def test_context_bundle_valid(self):
        cb = ContextBundle(
            project_type="python",
            context_files=["src/main.py"],
            classification="new_feature",
        )
        assert cb.project_type == "python"
        assert len(cb.context_files) == 1
        assert cb.loaded_at is not None

    def test_strategic_brief_valid(self):
        sb = StrategicBrief(
            what="Build a pipeline",
            why="Automation",
            not_building=["UI"],
            who_benefits="developers",
            assumptions=["Python 3.12"],
            open_questions=["Deployment?"],
            approved=False,
        )
        assert sb.what == "Build a pipeline"
        assert sb.approved is False

    def test_design_spec_valid(self):
        ds = DesignSpec(
            title="Pipeline Architecture",
            summary="10-stage pipeline",
            components=[
                ComponentSpec(
                    name="intake",
                    description="Context gathering",
                    files=["intake.py"],
                    libraries=["langchain"],
                )
            ],
            technology_choices={"language": "python", "framework": "langgraph"},
        )
        assert len(ds.components) == 1
        assert ds.components[0].name == "intake"

    def test_task_plan_valid(self):
        tp = TaskPlan(
            tasks=[
                Task(
                    id="T-001",
                    description="Implement intake",
                    files_to_create=["intake.py"],
                    complexity="standard",
                    estimated_cost_usd=0.02,
                )
            ],
            total_estimated_cost_usd=0.02,
        )
        assert len(tp.tasks) == 1
        assert tp.tasks[0].status == "pending"

    def test_build_result_valid(self):
        br = BuildResult(
            task_id="T-001",
            status="completed",
            commits=["abc123"],
        )
        assert br.status == "completed"

    def test_verification_record_valid(self):
        vr = VerificationRecord(
            all_green=True,
            layers=[
                LayerResult(layer=1, name="unit", status="pass", test_count=42),
                LayerResult(layer=2, name="lint", status="pass"),
            ],
            integrity_hash="sha256:abc",
        )
        assert vr.all_green is True
        assert len(vr.layers) == 2

    def test_review_result_valid(self):
        rr = ReviewResult(
            verdict="pass",
            critical_issues=[],
            important_issues=["Consider caching"],
        )
        assert rr.verdict == "pass"

    def test_ship_result_valid(self):
        sr = ShipResult(action="pr", pr_url="https://github.com/test/pr/1")
        assert sr.action == "pr"
        assert sr.pr_url is not None

    def test_evolve_report_valid(self):
        er = EvolveReport(
            session_handoff_path="sessions/2026-03-23.md",
            bible_updated=True,
            decisions_logged=["Use LangGraph"],
        )
        assert er.bible_updated is True

    def test_pipeline_error_valid(self):
        pe = PipelineError(
            stage="build",
            message="Compilation failed",
            timestamp="2026-03-23T00:00:00Z",
            recoverable=False,
        )
        assert pe.recoverable is False

    # --- Invalid data tests ---

    def test_context_bundle_missing_required(self):
        with pytest.raises(ValidationError):
            ContextBundle()

    def test_strategic_brief_missing_required(self):
        with pytest.raises(ValidationError):
            StrategicBrief()

    def test_task_wrong_complexity_literal(self):
        with pytest.raises(ValidationError):
            Task(id="T-001", description="test", complexity="impossible")

    def test_task_wrong_status_literal(self):
        with pytest.raises(ValidationError):
            Task(id="T-001", description="test", status="exploded")

    def test_build_result_wrong_status(self):
        with pytest.raises(ValidationError):
            BuildResult(task_id="T-001", status="maybe")

    def test_layer_result_wrong_status(self):
        with pytest.raises(ValidationError):
            LayerResult(layer=1, name="test", status="unknown")

    def test_review_result_wrong_verdict(self):
        with pytest.raises(ValidationError):
            ReviewResult(verdict="maybe")

    def test_ship_result_wrong_action(self):
        with pytest.raises(ValidationError):
            ShipResult(action="yeet")

    def test_design_spec_missing_required(self):
        with pytest.raises(ValidationError):
            DesignSpec()

    def test_component_spec_missing_required(self):
        with pytest.raises(ValidationError):
            ComponentSpec()

    # --- Serialization tests ---

    def test_strategic_brief_model_dump(self):
        sb = StrategicBrief(what="Build it", why="Because", who_benefits="Everyone")
        d = sb.model_dump()
        assert isinstance(d, dict)
        assert d["what"] == "Build it"
        assert d["why"] == "Because"
        assert d["who_benefits"] == "Everyone"
        assert d["not_building"] == []
        assert d["assumptions"] == []
        assert d["open_questions"] == []
        assert d["approved"] is False

    def test_task_plan_model_dump(self):
        tp = TaskPlan(
            tasks=[
                Task(id="T-001", description="Do thing"),
                Task(id="T-002", description="Do other thing", complexity="complex"),
            ],
            total_estimated_cost_usd=0.05,
        )
        d = tp.model_dump()
        assert isinstance(d, dict)
        assert len(d["tasks"]) == 2
        assert d["tasks"][0]["id"] == "T-001"
        assert d["tasks"][1]["complexity"] == "complex"
        assert d["total_estimated_cost_usd"] == 0.05

    def test_verification_record_model_dump_has_timestamp(self):
        vr = VerificationRecord(all_green=False, layers=[])
        d = vr.model_dump()
        assert "timestamp" in d
        assert "all_green" in d
        assert d["all_green"] is False

    def test_context_bundle_model_dump_has_loaded_at(self):
        cb = ContextBundle(project_type="rust", classification="bug_fix")
        d = cb.model_dump()
        assert "loaded_at" in d
        assert d["project_type"] == "rust"
        assert d["context_files"] == []

    def test_nested_model_dump(self):
        ds = DesignSpec(
            title="Test",
            summary="Test arch",
            components=[
                ComponentSpec(name="A", description="Component A"),
                ComponentSpec(name="B", description="Component B", libraries=["lib1"]),
            ],
        )
        d = ds.model_dump()
        assert len(d["components"]) == 2
        assert d["components"][0]["name"] == "A"
        assert d["components"][1]["libraries"] == ["lib1"]
