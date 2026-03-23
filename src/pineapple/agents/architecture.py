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
    from pineapple.llm import get_llm_client, has_any_llm_key, COST_ESTIMATES
    from tenacity import retry, stop_after_attempt, wait_exponential
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
"not_building" list — do not design components for excluded scope."""

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

## Instructions

Design the system architecture for this project. Your output must be a \
DesignSpec with:
- **title**: A concise architecture title (e.g., "Event-Driven Microservice Architecture")
- **summary**: 2-3 paragraph summary covering: the recommended approach, \
why it was chosen over alternatives, and key architectural decisions
- **components**: Break the system into 3-8 ComponentSpec items
- **technology_choices**: A dictionary mapping category to choice \
(e.g., {{"language": "Python 3.12", "framework": "FastAPI", "database": "PostgreSQL"}})
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
    )


# ---------------------------------------------------------------------------
# LLM call with retry (only defined if deps are available)
# ---------------------------------------------------------------------------


def _call_llm(system: str, user: str) -> tuple[DesignSpec, str]:
    """Call the LLM via the router and return a (DesignSpec, provider) tuple.

    Retries up to 3 times with exponential backoff for transient failures.
    """
    llm = get_llm_client(stage="architecture")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
    def _inner() -> DesignSpec:
        return llm.create(
            response_model=DesignSpec,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=_MAX_TOKENS,
        )

    return _inner(), llm.provider


# ---------------------------------------------------------------------------
# Error spec factory
# ---------------------------------------------------------------------------


def _make_error_spec(error: str) -> DesignSpec:
    """Create a stub DesignSpec for error cases."""
    return DesignSpec(
        title=f"Error generating architecture: {error}",
        summary="Architecture stage failed.",
        components=[],
        technology_choices={},
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
        spec, provider = _call_llm(_SYSTEM_PROMPT, user_prompt)

        # Force approved=False — human must approve at the interrupt gate
        spec.approved = False

        print(f"  [Architecture] Design spec generated (provider: {provider}):")
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
            "cost_total_usd": state.get("cost_total_usd", 0.0) + COST_ESTIMATES.get(provider, 0.03),
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
