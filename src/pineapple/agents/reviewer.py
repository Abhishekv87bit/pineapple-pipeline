"""Stage 7: Reviewer — review build results against the spec.

Uses the LLM router to generate a ReviewResult via Instructor.
FRESH CONTEXT: No knowledge of build or verify internals.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
from __future__ import annotations

from datetime import datetime, timezone

from pineapple.models import ReviewResult
from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Lazy imports for optional LLM dependencies
# ---------------------------------------------------------------------------

_HAS_LLM_DEPS = True
_IMPORT_ERROR: str | None = None

try:
    from pineapple.llm import get_llm_client, has_any_llm_key, COST_ESTIMATES, estimate_cost, _extract_usage, flush_traces
    from tenacity import retry, stop_after_attempt, wait_exponential
except ImportError as exc:
    _HAS_LLM_DEPS = False
    _IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MAX_TOKENS = 4096

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior code reviewer. You receive:
1. A design specification (what SHOULD have been built)
2. Build results (what WAS built)
3. Verification results (test outcomes)

Your job is to compare the implementation against the spec and tests, then
produce a verdict:
- "pass" — implementation matches spec, tests pass, ready to ship
- "retry" — fixable issues found, send back to builder
- "fail" — fundamental problems, needs human intervention

Be specific about issues found. Categorize them as critical, important, or minor."""

_USER_PROMPT_TEMPLATE = """\
## Design Specification
{design_spec}

## Build Results
{build_results}

## Verification Results
{verify_record}

Review the implementation against the spec and test results.
Produce a ReviewResult with your verdict and categorized issues."""


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------


def _call_llm(design_spec: str, build_results: str, verify_record: str, is_lightweight: bool = False) -> tuple[ReviewResult, str, float]:
    """Call the LLM via the router and return (ReviewResult, provider, cost_usd).

    Uses real token counts from the response when available, otherwise
    falls back to flat cost estimates.
    """
    llm = get_llm_client(stage="review")

    system = _SYSTEM_PROMPT
    if is_lightweight:
        system += (
            "\n\nIMPORTANT: This is a LIGHTWEIGHT path (bug fix / small change). "
            "Minimal or sparse build output is expected and acceptable. "
            "Do NOT flag empty or minimal results as critical issues. "
            "Only flag genuine implementation errors as critical."
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
    def _inner() -> ReviewResult:
        return llm.create(
            response_model=ReviewResult,
            system=system,
            messages=[{"role": "user", "content": _USER_PROMPT_TEMPLATE.format(
                design_spec=design_spec,
                build_results=build_results,
                verify_record=verify_record,
            )}],
            max_tokens=_MAX_TOKENS,
        )

    result = _inner()
    usage = _extract_usage(result, llm.provider)
    cost = estimate_cost(llm.provider, usage)
    return result, llm.provider, cost


# ---------------------------------------------------------------------------
# Fallback reviewer (no LLM)
# ---------------------------------------------------------------------------


def _review_fallback(build_results: list[dict], verify_record: dict | None, is_lightweight: bool = False) -> ReviewResult:
    """Produce a ReviewResult without LLM based on build/verify status."""
    # Check if any builds failed
    failed_builds = [r for r in build_results if r.get("status") == "failed"]

    # Check verification
    all_green = True
    if verify_record:
        all_green = verify_record.get("all_green", False)

    # Lightweight path (bug fixes): minimal build output is acceptable
    if is_lightweight and not failed_builds:
        return ReviewResult(
            verdict="pass",
            critical_issues=[],
            important_issues=[],
            minor_issues=["Lightweight path: minimal build output accepted", "Review performed without LLM"],
        )

    if failed_builds:
        return ReviewResult(
            verdict="retry",
            critical_issues=[f"Task {r['task_id']} failed: {r.get('errors', [])}" for r in failed_builds],
            important_issues=[],
            minor_issues=["Review performed without LLM — manual review recommended"],
        )
    elif not all_green:
        return ReviewResult(
            verdict="retry",
            critical_issues=[],
            important_issues=["Verification reported issues — check verify_record for details"],
            minor_issues=["Review performed without LLM — manual review recommended"],
        )
    else:
        return ReviewResult(
            verdict="pass",
            critical_issues=[],
            important_issues=[],
            minor_issues=["Review performed without LLM — manual review recommended"],
        )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def reviewer_node(state: PipelineState) -> dict:
    """Review build results against the design spec.

    FRESH CONTEXT: No knowledge of how the code was built or tested.
    Reads build_results, verify_record, and design_spec from state.

    Falls back gracefully if LLM dependencies or API key are unavailable.
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 7: Review] Project: {project_name}")

    build_results = state.get("build_results", [])
    verify_record = state.get("verify_record")
    design_spec_data = state.get("design_spec") or {}

    # Determine if we can use LLM
    use_llm = _HAS_LLM_DEPS and has_any_llm_key()

    if use_llm:
        try:
            print("  [Review] Calling LLM for code review...")
            result, provider, call_cost = _call_llm(
                design_spec=str(design_spec_data),
                build_results=str(build_results),
                verify_record=str(verify_record),
                is_lightweight=(state.get("path") == "lightweight"),
            )
            print(f"  [Review] Verdict (provider: {provider}, cost: ${call_cost:.4f}): {result.verdict}")
            print(f"    Critical: {len(result.critical_issues)}")
            print(f"    Important: {len(result.important_issues)}")
            print(f"    Minor: {len(result.minor_issues)}")

            # Flush LangFuse traces before returning
            flush_traces()

            return {
                "current_stage": "review",
                "review_result": result.model_dump(),
                "cost_total_usd": state.get("cost_total_usd", 0.0) + call_cost,
            }
        except Exception as e:
            msg = f"LLM review failed: {e}"
            print(f"  [Review] ERROR: {msg}, falling back to heuristic review")
    else:
        reason = _IMPORT_ERROR if not _HAS_LLM_DEPS else "No LLM API key set"
        print(f"  [Review] LLM unavailable ({reason}), using fallback reviewer.")

    # Fallback path
    result = _review_fallback(build_results, verify_record, is_lightweight=(state.get("path") == "lightweight"))
    print(f"  [Review] Verdict (fallback): {result.verdict}")

    return {
        "current_stage": "review",
        "review_result": result.model_dump(),
    }
