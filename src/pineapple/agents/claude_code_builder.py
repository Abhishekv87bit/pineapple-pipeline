"""Claude Code builder -- shells out to the `claude` CLI for iterative coding.

Instead of calling Gemini API, this builder invokes the `claude` CLI tool
with a prompt and parses the JSON output. Claude Code has its own built-in
tools (read/write/bash) so no tool scaffolding is needed.

Activated via: PINEAPPLE_BUILDER=claude_code
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# System prompt template written to a temp file for --append-system-prompt-file
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert software engineer implementing a task for the Pineapple Pipeline.

RULES:
- Write REAL production code, not stubs or placeholders
- Write files to the EXACT paths specified
- Follow the architecture spec precisely -- exact class names, exact field types
- Do NOT ask questions -- just implement

CRITICAL DOMAIN CONTEXT: "KFS" stands for "Kinetic Forge Studio" -- a kinetic sculpture
design application. It is NOT Kubernetes. A ".kfs.yaml" manifest describes 3D geometry,
materials, motion parameters, and simulation settings for kinetic sculptures.
Do NOT generate Kubernetes-related code, containers, volumes, or workload definitions.

PATH ENFORCEMENT: You MUST write files to the exact paths listed in files_to_create
and files_to_modify. Do NOT invent alternative paths, rename directories, or create
duplicate module trees.

TEST REQUIREMENTS: When generating test files, tests MUST import classes and functions
from the actual source modules. Do NOT define mock classes inline or redefine classes.
Use pytest fixtures and unittest.mock only for external dependencies.
"""


def _build_system_prompt(architecture_context: str) -> str:
    """Build the full system prompt, optionally injecting architecture context."""
    prompt = _SYSTEM_PROMPT_TEMPLATE
    if architecture_context:
        prompt += (
            "\n\n## Architecture Contract\n"
            "The following architecture specification is BINDING. You MUST:\n"
            "- Write files to the EXACT paths specified below\n"
            "- Use the EXACT class/function names from the interfaces below\n"
            "- Import from the EXACT modules specified below\n"
            "- Do NOT create files at different paths or with different names\n\n"
            + architecture_context
        )
    return prompt


def _build_user_prompt(
    task_description: str,
    design_summary: str,
    prior_context: str,
    files_to_create: list[str] | None,
    files_to_modify: list[str] | None,
    workspace_manifest: str,
    test_policy: str,
) -> str:
    """Assemble the user prompt from all inputs."""
    parts: list[str] = []

    if workspace_manifest:
        parts.append(f"## Workspace Map\n{workspace_manifest}")

    parts.append(f"## Task\n{task_description}")

    if files_to_create:
        parts.append(f"## Files to create\n" + "\n".join(f"- {p}" for p in files_to_create))

    if files_to_modify:
        parts.append(f"## Files to modify\n" + "\n".join(f"- {p}" for p in files_to_modify))

    if design_summary:
        parts.append(f"## Design context\n{design_summary}")

    if prior_context:
        parts.append(f"## Additional context\n{prior_context}")

    # Test policy instructions
    if test_policy == "none":
        parts.append(
            "## Test policy\n"
            "Do NOT run any tests. Just write all required files and finish."
        )
    elif test_policy == "import_only":
        parts.append(
            "## Test policy\n"
            "After writing code, verify imports work:\n"
            "  python -c 'import <module>'\n"
            "Replace <module> with the dotted path of your main module.\n"
            "If the import fails, fix the issue. Do NOT run full pytest."
        )
    else:  # "full"
        parts.append(
            "## Test policy\n"
            "After writing code, run pytest on your test files and fix any failures.\n"
            "Only finish when all tests pass (or you have tried at least twice)."
        )

    return "\n\n".join(parts)


def _collect_files_from_git_diff(workspace: str) -> list[dict]:
    """Use git diff HEAD to discover what files were written, then read them."""
    files: list[dict] = []
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--name-only"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Try against empty tree (no commits yet)
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
        changed_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        # Also check for untracked files that were newly written
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        untracked = [line.strip() for line in untracked_result.stdout.splitlines() if line.strip()]
        all_changed = list(dict.fromkeys(changed_files + untracked))  # deduplicate, preserve order

        for rel_path in all_changed:
            full_path = Path(workspace) / rel_path
            if full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    files.append({"path": rel_path, "content": content})
                except OSError:
                    pass
    except (subprocess.TimeoutExpired, OSError):
        pass
    return files


def _estimate_cost(prompt: str, response_text: str) -> float:
    """Rough token-based cost estimate when usage data is unavailable."""
    input_tokens = len(prompt) / 4
    output_tokens = len(response_text) / 4
    # $3/MTok input, $15/MTok output (Sonnet pricing)
    return (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000


def _parse_claude_output(raw_output: str) -> tuple[str, float]:
    """Parse JSON output from `claude -p --output-format json`.

    Returns (summary_text, cost_usd).
    """
    summary = "Task complete"
    cost = 0.0

    if not raw_output.strip():
        return summary, cost

    try:
        data = json.loads(raw_output.strip())
    except json.JSONDecodeError:
        # Not JSON — treat the whole output as summary text
        return raw_output.strip()[:500], cost

    # Try to extract a summary from known keys
    if isinstance(data, dict):
        # claude CLI JSON output format
        if "result" in data:
            result_val = data["result"]
            if isinstance(result_val, str):
                summary = result_val[:500]
        elif "messages" in data:
            msgs = data["messages"]
            if msgs and isinstance(msgs, list):
                last = msgs[-1]
                if isinstance(last, dict):
                    content = last.get("content", "")
                    if isinstance(content, str):
                        summary = content[:500]
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                summary = block.get("text", "")[:500]
                                break

        # Try to extract cost from usage info
        usage = data.get("usage") or data.get("cost_usd")
        if isinstance(usage, (int, float)):
            cost = float(usage)
        elif isinstance(usage, dict):
            input_tok = usage.get("input_tokens", 0)
            output_tok = usage.get("output_tokens", 0)
            cost = (input_tok * 3.0 + output_tok * 15.0) / 1_000_000

    return summary, cost


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_claude_code_task(
    task_description: str,
    workspace: str,
    design_summary: str = "",
    prior_context: str = "",
    files_to_create: list[str] | None = None,
    files_to_modify: list[str] | None = None,
    max_turns: int | None = None,
    architecture_context: str = "",
    workspace_manifest: str = "",
    test_policy: str = "full",
) -> tuple[list[dict], float, str]:
    """Run a Claude Code CLI session for a single build task.

    Shells out to the `claude` CLI with the task prompt. Claude Code has
    built-in file/terminal tools so no additional scaffolding is needed.

    Args:
        task_description: What to build.
        workspace: Absolute path to the workspace directory.
        design_summary: Architecture/design context.
        prior_context: Files from previous tasks + retry feedback.
        files_to_create: Expected new files.
        files_to_modify: Expected modified files.
        max_turns: Max agentic turns (default 15).
        architecture_context: Binding architecture excerpt (file paths, class names).
        workspace_manifest: Pre-built workspace listing (optional).
        test_policy: "full" | "import_only" | "none".

    Returns:
        Tuple of (files_written_dicts, cost_usd, summary).
        files_written_dicts is a list of {"path": str, "content": str}.
    """
    effective_max_turns = max_turns or 15
    model = os.environ.get("PINEAPPLE_CLAUDE_CODE_MODEL", "sonnet")

    # Build prompts
    system_prompt = _build_system_prompt(architecture_context)
    user_prompt = _build_user_prompt(
        task_description=task_description,
        design_summary=design_summary,
        prior_context=prior_context,
        files_to_create=files_to_create,
        files_to_modify=files_to_modify,
        workspace_manifest=workspace_manifest,
        test_policy=test_policy,
    )

    # Write system prompt to a temp file for --append-system-prompt-file
    system_prompt_file: str | None = None
    tmp_file = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix="pineapple_claude_system_",
            delete=False,
            encoding="utf-8",
        )
        tmp_file.write(system_prompt)
        tmp_file.flush()
        tmp_file.close()
        system_prompt_file = tmp_file.name
    except OSError:
        system_prompt_file = None

    # Build the CLI command
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--max-turns", str(effective_max_turns),
        "--model", model,
        "--no-session-persistence",
        "--allowedTools", "Edit,Write,Bash,Read",
    ]
    if system_prompt_file:
        cmd.extend(["--append-system-prompt-file", system_prompt_file])

    # Prompt is piped via stdin (not as positional arg — avoids shell quoting
    # issues and --allowedTools eating the prompt on Windows)

    # Run claude CLI
    raw_output = ""
    stderr_text = ""
    try:
        proc = subprocess.run(
            cmd,
            cwd=workspace,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=600,
            encoding="utf-8",
            errors="replace",
        )
        raw_output = proc.stdout or ""
        stderr_text = proc.stderr or ""

        if proc.returncode != 0:
            print(f"      [ClaudeCode] Non-zero exit ({proc.returncode}): {stderr_text[:300]}")
            # Try to collect any files written before the error
            files_written = _collect_files_from_git_diff(workspace)
            if files_written:
                summary = f"Partial: claude exited {proc.returncode} but wrote {len(files_written)} files"
                cost = _estimate_cost(user_prompt, raw_output)
                return files_written, cost, summary
            return [], 0.0, f"claude CLI failed (exit {proc.returncode}): {stderr_text[:300]}"

    except subprocess.TimeoutExpired:
        print("      [ClaudeCode] Timed out after 600s — collecting partial files")
        files_written = _collect_files_from_git_diff(workspace)
        cost = _estimate_cost(user_prompt, "")
        summary = f"Timed out, {len(files_written)} files partially written"
        return files_written, cost, summary

    except FileNotFoundError:
        return [], 0.0, "claude CLI not found — ensure 'claude' is on PATH"

    except OSError as exc:
        return [], 0.0, f"Failed to launch claude CLI: {exc}"

    finally:
        # Clean up temp file
        if system_prompt_file:
            try:
                os.unlink(system_prompt_file)
            except OSError:
                pass

    # Parse JSON output for summary and cost
    summary, cost = _parse_claude_output(raw_output)

    # If cost not available from JSON, estimate from prompt/response size
    if cost == 0.0:
        cost = _estimate_cost(user_prompt, raw_output)

    # Collect files written via git diff (most reliable approach)
    files_written = _collect_files_from_git_diff(workspace)

    if not files_written:
        print("      [ClaudeCode] No files detected via git diff")
        return [], cost, "No files written by Claude Code"

    print(f"      [ClaudeCode] Done. Files: {len(files_written)}, Cost: ${cost:.4f}")
    return files_written, cost, summary
