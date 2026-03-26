"""Stage 2: Architecture — Technical design and component breakdown.

Uses the LLM router to generate a structured DesignSpec via Instructor.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from pineapple.models import ComponentSpec, DesignSpec
from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Lazy imports for optional LLM dependencies
# ---------------------------------------------------------------------------

_HAS_LLM_DEPS = True
_IMPORT_ERROR: str | None = None

try:
    from pineapple.llm import call_with_retry, has_any_llm_key
except ImportError as exc:
    _HAS_LLM_DEPS = False
    _IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MAX_TOKENS = 4096

# ---------------------------------------------------------------------------
# System / user prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a principal software architect designing the technical architecture \
for a project that has already been through strategic review.

Your job:
1. Propose 2-3 distinct technical approaches based on the strategic brief.
2. Recommend ONE approach with clear justification.
3. Break the recommended design into concrete components (ComponentSpec).
4. Define technology choices (language, framework, libraries, infrastructure).

For each component, specify:
- **name**: Short identifier (e.g., "api_server", "auth_module")
- **description**: What this component does and why it exists
- **files**: Key files this component will create or modify
- **libraries**: External libraries/packages this component depends on

Be specific and practical. Every component must map to real code that a \
developer can implement. Avoid vague hand-waving.

The architecture must respect the scope defined in the strategic brief's \
"not_building" list — do not design components for excluded scope.

If context includes a codebase_summary, you MUST respect the existing technology stack.
Do NOT propose replacing existing technologies unless explicitly asked.
Propose MODIFICATIONS and ADDITIONS to the existing codebase, not a rewrite.

If locked decisions exist in the context, treat them as hard constraints:
- Never contradict a locked decision
- Reference the decision by name in your rationale"""

_USER_PROMPT_TEMPLATE = """\
## Strategic Brief

**What we are building:** {what}

**Why (real motivation):** {why}

**NOT building (scope exclusions):** {not_building}

**Who benefits:** {who_benefits}

**Assumptions:** {assumptions}

**Open questions from strategic review:** {open_questions}

## Additional Context

Project name: {project_name}
Project type: {project_type}
Original request: {request}

## Existing Codebase
{codebase_summary}

## Locked Decisions
{locked_decisions}

## Instructions

Design the system architecture for this project. Your output must be a \
DesignSpec with:
- **title**: A concise architecture title (e.g., "Event-Driven Microservice Architecture")
- **summary**: 2-3 paragraph summary covering: the recommended approach, \
why it was chosen over alternatives, and key architectural decisions
- **components**: Break the system into 3-8 ComponentSpec items
- **technology_choices_list**: A list of TechnologyChoice items, each with a \
"category" (e.g., "language", "framework", "database", "infrastructure", \
"testing") and a "choice" (e.g., "Python 3.12", "FastAPI", "PostgreSQL"). \
Include at least 3-5 technology choices covering language, framework, and \
key libraries.
- **approved**: Always set to false (human approves at the gate)"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_user_prompt(state: PipelineState) -> str:
    """Construct the user prompt from pipeline state."""
    request = state.get("request", "")
    project_name = state.get("project_name", "unknown")
    context_bundle = state.get("context_bundle") or {}
    strategic_brief = state.get("strategic_brief") or {}

    project_type = context_bundle.get("project_type", "unknown") if context_bundle else "unknown"

    codebase_summary = context_bundle.get("codebase_summary", {}) if context_bundle else {}
    project_memory = context_bundle.get("project_memory", {}) if context_bundle else {}
    locked_decisions = project_memory.get("locked_decisions", []) if project_memory else []

    return _USER_PROMPT_TEMPLATE.format(
        what=strategic_brief.get("what", "Unknown"),
        why=strategic_brief.get("why", "Unknown"),
        not_building=json.dumps(strategic_brief.get("not_building", []), indent=2),
        who_benefits=strategic_brief.get("who_benefits", "Unknown"),
        assumptions=json.dumps(strategic_brief.get("assumptions", []), indent=2),
        open_questions=json.dumps(strategic_brief.get("open_questions", []), indent=2),
        project_name=project_name,
        project_type=project_type,
        request=request,
        codebase_summary=json.dumps(codebase_summary, indent=2) if codebase_summary else "No existing codebase.",
        locked_decisions=json.dumps(locked_decisions, indent=2) if locked_decisions else "No locked decisions.",
    )


# ---------------------------------------------------------------------------
# LLM call with retry (only defined if deps are available)
# ---------------------------------------------------------------------------


def _call_llm(system: str, user: str) -> tuple[DesignSpec, str, float]:
    """Call the LLM via the router and return (DesignSpec, provider, cost_usd).

    Retries up to 3 times with exponential backoff for transient failures.
    Uses real token counts from the response when available, otherwise
    falls back to flat cost estimates.
    """
    return call_with_retry(
        stage="architecture",
        response_model=DesignSpec,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=_MAX_TOKENS,
    )


# ---------------------------------------------------------------------------
# Error spec factory
# ---------------------------------------------------------------------------


def _make_error_spec(error: str) -> DesignSpec:
    """Create a stub DesignSpec for error cases."""
    return DesignSpec(
        title=f"Error generating architecture: {error}",
        summary="Architecture stage failed.",
        components=[],
        technology_choices_list=[],
        approved=False,
    )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def architecture_node(state: PipelineState) -> dict:
    """Generate a technical architecture based on the strategic brief.

    This is Stage 2 of the pipeline. It takes the StrategicBrief from
    Stage 1 and produces a DesignSpec with components, technology choices,
    and a recommended approach.

    Falls back gracefully if:
    - LLM dependencies (instructor, anthropic) are not installed
    - ANTHROPIC_API_KEY is not set
    - The LLM call fails after retries
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 2: Architecture] Project: {project_name}")

    # --- Guard: missing LLM dependencies ---
    if not _HAS_LLM_DEPS:
        msg = (
            f"LLM dependencies not available ({_IMPORT_ERROR}). "
            "Install with: pip install 'pineapple-pipeline[llm]'"
        )
        print(f"  [Architecture] {msg}")
        spec = _make_error_spec(msg)
        return {
            "current_stage": "architecture",
            "design_spec": spec.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "architecture", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Guard: missing API key ---
    if not has_any_llm_key():
        msg = "No LLM API key set. Set GOOGLE_API_KEY (Gemini) or ANTHROPIC_API_KEY (Claude)."
        print(f"  [Architecture] {msg}")
        spec = _make_error_spec(msg)
        return {
            "current_stage": "architecture",
            "design_spec": spec.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "architecture", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Guard: missing strategic brief ---
    strategic_brief = state.get("strategic_brief")
    if not strategic_brief:
        msg = "No strategic brief found in state. Stage 1 may have failed."
        print(f"  [Architecture] {msg}")
        spec = _make_error_spec(msg)
        return {
            "current_stage": "architecture",
            "design_spec": spec.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "architecture", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Main path: call LLM ---
    try:
        user_prompt = _build_user_prompt(state)

        print("  [Architecture] Calling LLM for design spec...")
        spec, provider, call_cost = _call_llm(_SYSTEM_PROMPT, user_prompt)

        # Force approved=False — human must approve at the interrupt gate
        spec.approved = False

        print(f"  [Architecture] Design spec generated (provider: {provider}, cost: ${call_cost:.4f}):")
        print(f"    Title: {spec.title}")
        print(f"    Components: {len(spec.components)}")
        for comp in spec.components:
            print(f"      - {comp.name}: {comp.description[:80]}...")
        print(f"    Technology choices: {len(spec.technology_choices)} entries")
        for category, choice in spec.technology_choices.items():
            print(f"      - {category}: {choice}")

        return {
            "current_stage": "architecture",
            "design_spec": spec.model_dump(),
            "cost_total_usd": state.get("cost_total_usd", 0.0) + call_cost,
        }

    except Exception as e:
        msg = f"LLM call failed after retries: {e}"
        print(f"  [Architecture] ERROR: {msg}")
        spec = _make_error_spec(str(e))
        return {
            "current_stage": "architecture",
            "design_spec": spec.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "architecture", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }
