"""Stage 7: Reviewer — review build results against the spec.

Uses the LLM router to generate a ReviewResult via Instructor.
FRESH CONTEXT: No knowledge of build or verify internals.
Install dependencies with: pip install 'pineapple-pipeline[llm]'

Supports auto-chunked review: when the diff is large (configurable via
PINEAPPLE_REVIEW_CHUNK_FILES and PINEAPPLE_REVIEW_CHUNK_LINES env vars),
changed files are grouped by top-level directory and reviewed independently,
then merged by severity.
"""
from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from pineapple.models import ReviewResult
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
# Chunking thresholds (configurable via env vars)
# ---------------------------------------------------------------------------

_CHUNK_FILES_THRESHOLD = int(os.environ.get("PINEAPPLE_REVIEW_CHUNK_FILES", "50"))
_CHUNK_LINES_THRESHOLD = int(os.environ.get("PINEAPPLE_REVIEW_CHUNK_LINES", "5000"))

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior code reviewer. You receive:
1. A design specification (what SHOULD have been built)
2. Build results (what WAS built)
3. Verification results (test outcomes)
4. An architecture document (optional — when provided, it defines exact file paths, interfaces, and build order)

Your job is to compare the implementation against the spec and tests, then
produce a verdict:
- "pass" — implementation matches spec, tests pass, ready to ship
- "retry" — fixable issues found, send back to builder
- "fail" — fundamental problems, needs human intervention

If an architecture document is provided, check that files are at the correct paths, interfaces match the spec, and dependencies follow the build order.

Be specific about issues found. Categorize them as critical, important, or minor."""

_USER_PROMPT_TEMPLATE = """\
## Design Specification
{design_spec}

## Architecture Document
{architecture_doc}

## Build Results
{build_results}

## Verification Results
{verify_record}

Review the implementation against the spec and test results.
Produce a ReviewResult with your verdict and categorized issues."""

_CHUNK_SYSTEM_PROMPT = """\
You are a senior code reviewer reviewing a SUBSET of changes in module: {module_name}.
You receive:
1. A design specification (what SHOULD have been built)
2. Files changed in this module
3. Build results relevant to this module
4. Verification results (test outcomes)

Your job is to review ONLY this module's changes against the spec and tests, then
produce a verdict:
- "pass" — this module's implementation matches spec, tests pass
- "retry" — fixable issues found in this module
- "fail" — fundamental problems in this module

Be specific about issues found. Categorize them as critical, important, or minor.
Prefix each issue with the module name in brackets, e.g. [kfs_core] Missing validation."""

_CHUNK_USER_PROMPT_TEMPLATE = """\
## Module Under Review: {module_name}
Files in this chunk: {file_list}

## Design Specification
{design_spec}

## Build Results (filtered to this module)
{build_results}

## Verification Results
{verify_record}

Review this module's implementation against the spec and test results.
Produce a ReviewResult with your verdict and categorized issues."""


# ---------------------------------------------------------------------------
# Diff chunking
# ---------------------------------------------------------------------------


def chunk_diff_by_module(
    changed_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group changed files by top-level directory (module).

    Args:
        changed_files: List of dicts, each with at least a ``path`` key
            and optionally a ``lines_changed`` key (int, default 1).

    Returns:
        List of chunk dicts, each containing:
            - module: str — top-level directory name (or "_root" for root files)
            - files: list[dict] — the file entries in this chunk
            - file_count: int
            - lines_changed: int — sum of lines_changed across files
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for entry in changed_files:
        path = entry.get("path", "")
        # Normalise separators
        path = path.replace("\\", "/")
        parts = path.split("/")
        module = parts[0] if len(parts) > 1 else "_root"
        groups[module].append(entry)

    chunks = []
    for module, files in sorted(groups.items()):
        total_lines = sum(f.get("lines_changed", 1) for f in files)
        chunks.append({
            "module": module,
            "files": files,
            "file_count": len(files),
            "lines_changed": total_lines,
        })

    return chunks


def _should_chunk(changed_files: list[dict[str, Any]]) -> bool:
    """Decide whether the diff should be chunked based on thresholds."""
    if not changed_files:
        return False
    total_files = len(changed_files)
    total_lines = sum(f.get("lines_changed", 1) for f in changed_files)
    return total_files > _CHUNK_FILES_THRESHOLD or total_lines > _CHUNK_LINES_THRESHOLD


def _merge_chunk_results(chunk_results: list[dict[str, Any]]) -> ReviewResult:
    """Merge multiple chunk ReviewResults into a single ReviewResult.

    Verdict priority: fail > retry > pass.
    Issues are concatenated and deduplicated.
    """
    all_critical: list[str] = []
    all_important: list[str] = []
    all_minor: list[str] = []
    worst_verdict = "pass"

    verdict_rank = {"pass": 0, "retry": 1, "fail": 2}

    for cr in chunk_results:
        result = cr["result"]
        v = result.get("verdict", "pass") if isinstance(result, dict) else result.verdict
        if verdict_rank.get(v, 0) > verdict_rank.get(worst_verdict, 0):
            worst_verdict = v

        if isinstance(result, dict):
            all_critical.extend(result.get("critical_issues", []))
            all_important.extend(result.get("important_issues", []))
            all_minor.extend(result.get("minor_issues", []))
        else:
            all_critical.extend(result.critical_issues)
            all_important.extend(result.important_issues)
            all_minor.extend(result.minor_issues)

    # Deduplicate while preserving order
    def _dedup(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return ReviewResult(
        verdict=worst_verdict,
        critical_issues=_dedup(all_critical),
        important_issues=_dedup(all_important),
        minor_issues=_dedup(all_minor),
    )


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------


def _call_llm(design_spec: str, build_results: str, verify_record: str, is_lightweight: bool = False, architecture_doc: str = "") -> tuple[ReviewResult, str, float]:
    """Call the LLM via the router and return (ReviewResult, provider, cost_usd).

    Uses real token counts from the response when available, otherwise
    falls back to flat cost estimates.
    """
    system = _SYSTEM_PROMPT
    if is_lightweight:
        system += (
            "\n\nIMPORTANT: This is a LIGHTWEIGHT path (bug fix / small change). "
            "Minimal or sparse build output is expected and acceptable. "
            "Do NOT flag empty or minimal results as critical issues. "
            "Only flag genuine implementation errors as critical."
        )

    return call_with_retry(
        stage="review",
        response_model=ReviewResult,
        system=system,
        messages=[{"role": "user", "content": _USER_PROMPT_TEMPLATE.format(
            design_spec=design_spec,
            architecture_doc=architecture_doc or "(none provided)",
            build_results=build_results,
            verify_record=verify_record,
        )}],
        max_tokens=_MAX_TOKENS,
    )


def _call_llm_chunk(
    module_name: str,
    file_list: list[str],
    design_spec: str,
    build_results: str,
    verify_record: str,
    is_lightweight: bool = False,
    architecture_doc: str = "",
) -> tuple[ReviewResult, str, float]:
    """Call the LLM for a single chunk/module review."""
    system = _CHUNK_SYSTEM_PROMPT.format(module_name=module_name)
    if is_lightweight:
        system += (
            "\n\nIMPORTANT: This is a LIGHTWEIGHT path (bug fix / small change). "
            "Minimal or sparse build output is expected and acceptable. "
            "Do NOT flag empty or minimal results as critical issues. "
            "Only flag genuine implementation errors as critical."
        )

    return call_with_retry(
        stage="review",
        response_model=ReviewResult,
        system=system,
        messages=[{"role": "user", "content": _CHUNK_USER_PROMPT_TEMPLATE.format(
            module_name=module_name,
            file_list=", ".join(file_list),
            design_spec=design_spec,
            build_results=build_results,
            verify_record=verify_record,
        )}],
        max_tokens=_MAX_TOKENS,
    )


# ---------------------------------------------------------------------------
# Claude Code CLI reviewer
# ---------------------------------------------------------------------------


def _call_claude_code_reviewer(
    system: str,
    user: str,
) -> tuple[ReviewResult, str, float]:
    """Use Claude Code CLI to generate a ReviewResult when Gemini fails."""
    import json as _json
    import os
    import re
    import subprocess

    prompt = f"""{system}

---

{user}

---

IMPORTANT: Respond with ONLY valid JSON matching this schema (no markdown, no explanation):
{{
  "verdict": "pass|retry|fail",
  "critical_issues": ["..."],
  "important_issues": ["..."],
  "minor_issues": ["..."]
}}
"""

    model = os.environ.get("PINEAPPLE_CLAUDE_CODE_MODEL", "sonnet")
    proc = subprocess.run(
        [
            "claude.cmd", "-p",
            "--output-format", "json",
            "--max-turns", "3",
            "--model", model,
            "--no-session-persistence",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
    )

    raw = proc.stdout or ""
    try:
        cc_output = _json.loads(raw)
        result_text = cc_output.get("result", "")
        cost = cc_output.get("total_cost_usd", 0.0)
    except (_json.JSONDecodeError, TypeError):
        result_text = raw
        cost = 0.0

    json_match = re.search(r'\{[\s\S]*"verdict"[\s\S]*\}', result_text)
    if json_match:
        review_data = _json.loads(json_match.group())
    else:
        raise ValueError(f"Claude Code did not return valid ReviewResult JSON: {result_text[:200]}")

    result = ReviewResult(**review_data)
    return result, "claude_code", cost


# ---------------------------------------------------------------------------
# Claude Code CLI chunk reviewer
# ---------------------------------------------------------------------------


def _call_claude_code_chunk(
    module_name: str,
    file_list: list[str],
    design_spec: str,
    build_results: str,
    verify_record: str,
    is_lightweight: bool = False,
    architecture_doc: str = "",
) -> tuple[ReviewResult, str, float]:
    """Use Claude Code CLI to review a single chunk/module."""
    import json as _json
    import os
    import re
    import subprocess

    system = _CHUNK_SYSTEM_PROMPT.format(module_name=module_name)
    if is_lightweight:
        system += (
            "\n\nIMPORTANT: This is a LIGHTWEIGHT path (bug fix / small change). "
            "Minimal or sparse build output is expected and acceptable. "
            "Do NOT flag empty or minimal results as critical issues. "
            "Only flag genuine implementation errors as critical."
        )

    user = _CHUNK_USER_PROMPT_TEMPLATE.format(
        module_name=module_name,
        file_list=", ".join(file_list),
        design_spec=design_spec,
        build_results=build_results,
        verify_record=verify_record,
    )

    prompt = f"""{system}

---

{user}

---

IMPORTANT: Respond with ONLY valid JSON matching this schema (no markdown, no explanation):
{{
  "verdict": "pass|retry|fail",
  "critical_issues": ["..."],
  "important_issues": ["..."],
  "minor_issues": ["..."]
}}
"""

    model = os.environ.get("PINEAPPLE_CLAUDE_CODE_MODEL", "sonnet")
    proc = subprocess.run(
        [
            "claude.cmd", "-p",
            "--output-format", "json",
            "--max-turns", "3",
            "--model", model,
            "--no-session-persistence",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
    )

    raw = proc.stdout or ""
    try:
        cc_output = _json.loads(raw)
        result_text = cc_output.get("result", "")
        cost = cc_output.get("total_cost_usd", 0.0)
    except (_json.JSONDecodeError, TypeError):
        result_text = raw
        cost = 0.0

    json_match = re.search(r'\{[\s\S]*"verdict"[\s\S]*\}', result_text)
    if json_match:
        review_data = _json.loads(json_match.group())
    else:
        raise ValueError(f"Claude Code did not return valid ReviewResult JSON: {result_text[:200]}")

    result = ReviewResult(**review_data)
    return result, "claude_code", cost


# ---------------------------------------------------------------------------
# Chunked LLM review (parallel dispatch)
# ---------------------------------------------------------------------------


def _review_chunked_llm(
    chunks: list[dict[str, Any]],
    design_spec: str,
    build_results: str,
    verify_record: str,
    is_lightweight: bool = False,
    architecture_doc: str = "",
) -> tuple[ReviewResult, float]:
    """Dispatch parallel LLM reviews for each chunk, merge results.

    Returns (merged_ReviewResult, total_cost_usd).
    """
    chunk_results: list[dict[str, Any]] = []
    total_cost = 0.0

    def _review_one_chunk(chunk: dict) -> dict[str, Any]:
        module = chunk["module"]
        file_paths = [f.get("path", "") for f in chunk["files"]]
        try:
            result, provider, cost = _call_claude_code_chunk(
                module_name=module,
                file_list=file_paths,
                design_spec=design_spec,
                build_results=build_results,
                verify_record=verify_record,
                is_lightweight=is_lightweight,
                architecture_doc=architecture_doc,
            )
        except Exception as cc_err:
            print(f"    [Chunk: {module}] Claude Code CLI failed: {str(cc_err)[:50]}")
            print(f"    [Chunk: {module}] Falling back to Gemini...")
            result, provider, cost = _call_llm_chunk(
                module_name=module,
                file_list=file_paths,
                design_spec=design_spec,
                build_results=build_results,
                verify_record=verify_record,
                is_lightweight=is_lightweight,
                architecture_doc=architecture_doc,
            )
        return {"module": module, "result": result.model_dump(), "provider": provider, "cost": cost}

    # Dispatch in parallel using ThreadPoolExecutor
    max_workers = min(len(chunks), 5)  # Cap at 5 concurrent reviews
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_review_one_chunk, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            cr = future.result()
            chunk_results.append(cr)
            total_cost += cr["cost"]
            print(f"    [Chunk: {cr['module']}] Verdict: {cr['result']['verdict']} "
                  f"(provider: {cr['provider']}, cost: ${cr['cost']:.4f})")

    merged = _merge_chunk_results(chunk_results)
    return merged, total_cost


# ---------------------------------------------------------------------------
# Chunked fallback review (no LLM)
# ---------------------------------------------------------------------------


def _review_chunked_fallback(
    chunks: list[dict[str, Any]],
    build_results: list[dict],
    verify_record: dict | None,
    is_lightweight: bool = False,
) -> ReviewResult:
    """Produce chunk-aware fallback ReviewResult without LLM."""
    chunk_results = []
    for chunk in chunks:
        module = chunk["module"]
        # Run per-chunk fallback
        fr = _review_fallback(build_results, verify_record, is_lightweight=is_lightweight)
        # Tag issues with module name
        tagged = ReviewResult(
            verdict=fr.verdict,
            critical_issues=[f"[{module}] {i}" for i in fr.critical_issues],
            important_issues=[f"[{module}] {i}" for i in fr.important_issues],
            minor_issues=[f"[{module}] {i}" if not i.startswith("[") else i for i in fr.minor_issues],
        )
        chunk_results.append({"module": module, "result": tagged.model_dump()})

    return _merge_chunk_results(chunk_results)


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

    When ``changed_files`` is present in state and exceeds the chunking
    thresholds (PINEAPPLE_REVIEW_CHUNK_FILES / PINEAPPLE_REVIEW_CHUNK_LINES),
    the review is automatically split by top-level directory and dispatched
    in parallel (LLM path) or sequentially (fallback path).

    Falls back gracefully if LLM dependencies or API key are unavailable.
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 7: Review] Project: {project_name}")

    build_results = state.get("build_results", [])
    verify_record = state.get("verify_record")
    design_spec_data = state.get("design_spec") or {}
    architecture_doc = design_spec_data.get("_raw_document", "") or ""
    changed_files = state.get("changed_files") or []
    is_lightweight = state.get("path") == "lightweight"

    # Determine if we can use LLM
    use_llm = _HAS_LLM_DEPS and has_any_llm_key()

    # Determine if we should chunk the review
    do_chunk = _should_chunk(changed_files)
    chunks = chunk_diff_by_module(changed_files) if do_chunk else []

    if do_chunk:
        total_files = len(changed_files)
        total_lines = sum(f.get("lines_changed", 1) for f in changed_files)
        print(f"  [Review] Auto-chunking enabled: {total_files} files, {total_lines} lines "
              f"across {len(chunks)} modules "
              f"(thresholds: files>{_CHUNK_FILES_THRESHOLD}, lines>{_CHUNK_LINES_THRESHOLD})")
        for c in chunks:
            print(f"    Module '{c['module']}': {c['file_count']} files, {c['lines_changed']} lines")

    if use_llm or True:  # Claude Code CLI does not require Gemini/Anthropic SDK keys
        try:
            if do_chunk:
                print("  [Review] Dispatching chunked LLM reviews...")
                result, call_cost = _review_chunked_llm(
                    chunks=chunks,
                    design_spec=str(design_spec_data),
                    build_results=str(build_results),
                    verify_record=str(verify_record),
                    is_lightweight=is_lightweight,
                    architecture_doc=architecture_doc,
                )
            else:
                # Claude Code CLI is primary; Gemini (call_with_retry) is fallback
                print("  [Review] Calling Claude Code CLI for code review...")
                try:
                    system = _SYSTEM_PROMPT
                    if is_lightweight:
                        system += (
                            "\n\nIMPORTANT: This is a LIGHTWEIGHT path (bug fix / small change). "
                            "Minimal or sparse build output is expected and acceptable. "
                            "Do NOT flag empty or minimal results as critical issues. "
                            "Only flag genuine implementation errors as critical."
                        )
                    user = _USER_PROMPT_TEMPLATE.format(
                        design_spec=str(design_spec_data),
                        architecture_doc=architecture_doc or "(none provided)",
                        build_results=str(build_results),
                        verify_record=str(verify_record),
                    )
                    result, provider, call_cost = _call_claude_code_reviewer(system, user)
                except Exception as cc_err:
                    print(f"  [Review] Claude Code CLI failed: {str(cc_err)[:100]}")
                    if use_llm:
                        print("  [Review] Falling back to Gemini...")
                        result, provider, call_cost = _call_llm(
                            design_spec=str(design_spec_data),
                            build_results=str(build_results),
                            verify_record=str(verify_record),
                            is_lightweight=is_lightweight,
                            architecture_doc=architecture_doc,
                        )
                    else:
                        raise

            print(f"  [Review] Verdict: {result.verdict} (cost: ${call_cost:.4f})")
            print(f"    Critical: {len(result.critical_issues)}")
            print(f"    Important: {len(result.important_issues)}")
            print(f"    Minor: {len(result.minor_issues)}")

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
    if do_chunk:
        result = _review_chunked_fallback(chunks, build_results, verify_record, is_lightweight=is_lightweight)
    else:
        result = _review_fallback(build_results, verify_record, is_lightweight=is_lightweight)
    print(f"  [Review] Verdict (fallback): {result.verdict}")

    return {
        "current_stage": "review",
        "review_result": result.model_dump(),
    }
