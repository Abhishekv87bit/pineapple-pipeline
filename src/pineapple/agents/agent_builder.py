"""Agentic builder — Gemini with file/terminal tools for iterative coding.

Instead of generating all code in one shot, this builder gives Gemini tools
to read/write files and run commands. It iterates until tests pass or max
turns is reached.

Activated via: PINEAPPLE_BUILDER=agent (default is "single_shot")
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# Max conversation turns before giving up
MAX_TURNS = int(os.environ.get("PINEAPPLE_AGENT_MAX_TURNS", "25"))

# Per-turn LLM timeout in seconds
_AGENT_TURN_TIMEOUT = int(os.environ.get("PINEAPPLE_AGENT_TURN_TIMEOUT", "90"))


# ---------------------------------------------------------------------------
# Tool definitions (Gemini function calling format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file in the workspace. Returns the file text or an error.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from workspace root (e.g. 'src/main.py')",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file in the workspace. Creates parent directories if needed. Returns 'OK' or error.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from workspace root",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command in the workspace. Returns stdout+stderr (truncated to 3000 chars). Use for: pytest, python, pip, ls, cat, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (e.g. 'pytest tests/test_login.py -v')",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory. Returns newline-separated file paths.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path (e.g. 'src/' or '.')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "task_complete",
        "description": "Signal that the task is done. Call this when all files are written and tests pass.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what was implemented",
                },
            },
            "required": ["summary"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

def _exec_read_file(workspace: str, path: str) -> str:
    """Read a file from the workspace."""
    filepath = Path(workspace) / path
    if not filepath.is_file():
        return f"ERROR: File not found: {path}"
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
        if len(content) > 10000:
            return content[:10000] + f"\n... (truncated, {len(content)} total chars)"
        return content
    except OSError as exc:
        return f"ERROR: {exc}"


def _exec_write_file(workspace: str, path: str, content: str, allowed_paths: set[str] | None = None) -> str:
    """Write a file to the workspace."""
    filepath = Path(workspace) / path
    # Security: prevent path traversal
    try:
        resolved = filepath.resolve()
        ws_resolved = Path(workspace).resolve()
        if not str(resolved).startswith(str(ws_resolved)):
            return f"ERROR: Path traversal detected: {path}"
    except (OSError, ValueError):
        return f"ERROR: Invalid path: {path}"

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        result = f"OK: Wrote {len(content)} chars to {path}"
    except OSError as exc:
        return f"ERROR: {exc}"

    # Soft warning if path is outside the architecture spec
    rel_path = path.replace("\\", "/")
    if allowed_paths and rel_path not in allowed_paths:
        # Check if it's a common scaffolding file we should allow
        basename = os.path.basename(rel_path)
        if basename not in ("__init__.py", "conftest.py", "pytest.ini", ".env"):
            if not rel_path.startswith("tests/"):
                return (
                    f"WARNING: File written but path '{rel_path}' is NOT in the architecture spec. "
                    f"Expected paths: {sorted(list(allowed_paths))[:10]}. "
                    "Consider moving this file to the correct architecture path."
                )
    return result


def _exec_run_command(workspace: str, command: str) -> str:
    """Run a command in the workspace. Timeout 60s, output capped at 3000 chars."""
    # Security: block dangerous commands
    dangerous = ["rm -rf /", "del /s /q", "format", "mkfs", ":(){", "fork"]
    if any(d in command.lower() for d in dangerous):
        return "ERROR: Dangerous command blocked"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=workspace,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        if len(output) > 3000:
            output = output[:3000] + f"\n... (truncated)"
        return f"Exit code: {result.returncode}\n{output}" if output else f"Exit code: {result.returncode} (no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 60s"
    except OSError as exc:
        return f"ERROR: {exc}"


def _exec_list_files(workspace: str, path: str) -> str:
    """List files in a directory."""
    dirpath = Path(workspace) / path
    if not dirpath.is_dir():
        return f"ERROR: Not a directory: {path}"
    try:
        files = []
        for item in sorted(dirpath.rglob("*")):
            if item.is_file() and "__pycache__" not in str(item) and ".git" not in str(item):
                files.append(str(item.relative_to(Path(workspace))))
        if len(files) > 100:
            return "\n".join(files[:100]) + f"\n... ({len(files)} total files)"
        return "\n".join(files) if files else "(empty directory)"
    except OSError as exc:
        return f"ERROR: {exc}"


def _execute_tool(workspace: str, tool_name: str, args: dict, allowed_paths: set[str] | None = None) -> str:
    """Dispatch a tool call to the appropriate executor."""
    if tool_name == "read_file":
        return _exec_read_file(workspace, args.get("path", ""))
    elif tool_name == "write_file":
        return _exec_write_file(workspace, args.get("path", ""), args.get("content", ""), allowed_paths)
    elif tool_name == "run_command":
        return _exec_run_command(workspace, args.get("command", ""))
    elif tool_name == "list_files":
        return _exec_list_files(workspace, args.get("path", "."))
    elif tool_name == "task_complete":
        return "TASK_COMPLETE: " + args.get("summary", "done")
    else:
        return f"ERROR: Unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# Agent conversation loop
# ---------------------------------------------------------------------------

def run_agent_task(
    task_description: str,
    workspace: str,
    design_summary: str = "",
    prior_context: str = "",
    files_to_create: list[str] | None = None,
    files_to_modify: list[str] | None = None,
    max_turns: int | None = None,
    architecture_context: str = "",
    allowed_paths: set[str] | None = None,
    workspace_manifest: str = "",
    test_policy: str = "full",
) -> tuple[list[dict], float, str]:
    """Run a Gemini agent conversation for a single build task.

    The agent has tools to read/write files and run commands.
    It iterates until calling task_complete or hitting max_turns.

    Args:
        task_description: What to build.
        workspace: Absolute path to the workspace directory.
        design_summary: Architecture context.
        prior_context: Files from previous tasks + retry feedback.
        files_to_create: Expected files to create.
        files_to_modify: Expected files to modify.
        max_turns: Override for MAX_TURNS.
        architecture_context: Excerpt from the architecture document relevant to
            this task.  When provided, injected into the agent system message as
            a binding contract for file paths, class names, and imports.
        allowed_paths: Set of relative paths the agent is expected to write.
            Files written outside this set get a soft WARNING (not blocked).
        workspace_manifest: Pre-built listing of workspace files. When provided,
            replaces the default "list existing files" instruction.
        test_policy: Controls test execution level. "full" runs full pytest
            (Phase 5 / last phase behavior). "import_only" checks that files
            import without errors (Phase 1-4 behavior). "none" skips all tests.

    Returns:
        Tuple of (files_written_dicts, cost_usd, summary).
        files_written_dicts is a list of {"path": str, "content": str}.
    """
    from google import genai
    from google.genai import types
    from dotenv import load_dotenv
    load_dotenv(override=True)

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("No Google API key found (GOOGLE_API_KEY or GEMINI_API_KEY)")

    client = genai.Client(api_key=api_key)
    turns = max_turns or MAX_TURNS

    # Build the system instruction
    system = (
        "You are an expert software engineer with access to file and terminal tools.\n"
        "You are implementing a task from a project plan.\n\n"
        "WORKFLOW:\n"
        "1. Read existing files to understand the codebase\n"
        "2. Write implementation files\n"
        "3. Write test files\n"
    )
    if test_policy == "none":
        system += (
            "\n\nWORKFLOW:\n"
            "1. Read any existing files you need to modify\n"
            "2. Write all code files\n"
            "3. Do NOT run any tests — just write the code\n"
            "4. Call task_complete with a summary when all files are written\n"
        )
    elif test_policy == "import_only":
        system += (
            "\n\nWORKFLOW:\n"
            "1. Read any existing files you need to modify\n"
            "2. Write all code files\n"
            "3. Run a quick import check: run_command('python -c \"import importlib; "
            "importlib.import_module(\\\"<module_path>\\\")\"\\')\n"
            "   Replace <module_path> with the dotted path of your main file (e.g., backend.app.models.module)\n"
            "   If the import fails, fix the syntax error and retry ONCE\n"
            "4. Do NOT run pytest — full tests run in a later phase\n"
            "5. Call task_complete with a summary when all files are written and imports work\n"
        )
    else:
        system += (
            "4. Run tests with run_command('pytest <test_file> -v')\n"
            "5. If tests fail, read the errors, fix the code, re-run\n"
            "6. When tests pass, call task_complete with a summary\n"
        )
    system += (
        "\nRULES:\n"
        "- Write REAL code, not stubs or placeholders\n"
        "- Always run tests after writing code\n"
        "- Fix errors iteratively — don't give up\n"
        "- Use environment variables for secrets, never hardcode\n"
        "- Call task_complete when done\n"
    )

    if architecture_context:
        system += (
            "\n\n## Architecture Contract\n"
            "The following architecture specification is BINDING. You MUST:\n"
            "- Write files to the EXACT paths specified below\n"
            "- Use the EXACT class/function names from the interfaces below\n"
            "- Import from the EXACT modules specified below\n"
            "- Do NOT create files at different paths or with different names\n\n"
            + architecture_context
        )

    # Build the initial user message
    if workspace_manifest:
        user_msg = f"## Workspace Map\n{workspace_manifest}\n\n## Task\n{task_description}\n\n"
        if files_to_create:
            user_msg += f"## Files to create\n{', '.join(files_to_create)}\n\n"
        if files_to_modify:
            user_msg += f"## Files to modify\n{', '.join(files_to_modify)}\n\n"
        if design_summary:
            user_msg += f"## Design context\n{design_summary}\n\n"
        if prior_context:
            user_msg += f"## Additional context\n{prior_context}\n\n"
        user_msg += "Use the workspace map above to know what files exist and where to write. Do NOT call list_files('.') — the map already shows you everything."
    else:
        user_msg = f"## Task\n{task_description}\n\n"
        if files_to_create:
            user_msg += f"## Files to create\n{', '.join(files_to_create)}\n\n"
        if files_to_modify:
            user_msg += f"## Files to modify\n{', '.join(files_to_modify)}\n\n"
        if design_summary:
            user_msg += f"## Design context\n{design_summary}\n\n"
        if prior_context:
            user_msg += f"## Additional context\n{prior_context}\n\n"
        user_msg += "Start by listing existing files to understand the workspace, then implement the task."

    # Convert our tool definitions to Gemini format
    gemini_tools = types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["parameters"],
        )
        for t in TOOLS
    ])

    # Track files written by the agent
    files_written: dict[str, str] = {}  # path -> content
    total_cost = 0.0
    summary = "Task incomplete — max turns reached"

    # Conversation history
    contents = [types.Content(role="user", parts=[types.Part(text=user_msg)])]

    for turn in range(turns):
        # Call Gemini with per-turn timeout
        try:
            def _gemini_call() -> Any:
                return client.models.generate_content(
                    model=os.environ.get("PINEAPPLE_MODEL_GEMINI", "gemini-2.5-flash"),
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        tools=[gemini_tools],
                        temperature=0.2,
                        max_output_tokens=8192,
                    ),
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_gemini_call)
                try:
                    response = future.result(timeout=_AGENT_TURN_TIMEOUT)
                except FuturesTimeout:
                    print(f"      [Agent] Turn {turn+1}: LLM call timed out after {_AGENT_TURN_TIMEOUT}s")
                    break
        except Exception as exc:
            _logger.error("Gemini call failed on turn %d: %s", turn + 1, exc)
            break

        # Estimate cost (rough)
        total_cost += 0.001  # ~$0.001 per call for Gemini Flash

        # Check if response has function calls
        if not response.candidates or not response.candidates[0].content.parts:
            break

        response_parts = response.candidates[0].content.parts
        contents.append(types.Content(role="model", parts=response_parts))

        # Process each part (text or function call)
        function_responses = []
        task_done = False

        for part in response_parts:
            if part.function_call:
                fc = part.function_call
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                print(f"      [Agent] Turn {turn+1}: {tool_name}({', '.join(f'{k}={repr(v)[:50]}' for k,v in tool_args.items())})")

                # Execute the tool
                result = _execute_tool(workspace, tool_name, tool_args, allowed_paths)

                # Track written files
                if tool_name == "write_file" and result.startswith("OK"):
                    files_written[tool_args["path"]] = tool_args["content"]

                # Check for task completion
                if tool_name == "task_complete":
                    summary = tool_args.get("summary", "done")
                    task_done = True

                function_responses.append(
                    types.Part(function_response=types.FunctionResponse(
                        name=tool_name,
                        response={"result": result},
                    ))
                )
            elif part.text:
                # Model is thinking/explaining — that's fine
                pass

        # Send function responses back if there were any
        if function_responses:
            contents.append(types.Content(role="user", parts=function_responses))

        if task_done:
            print(f"      [Agent] Task complete after {turn+1} turns: {summary[:100]}")
            break
    else:
        print(f"      [Agent] Max turns ({turns}) reached without completion")

    # Convert to list of dicts for BuildResult compatibility
    files_list = [{"path": p, "content": c} for p, c in files_written.items()]

    return files_list, total_cost, summary
