"""Stage 4: Setup — prepare workspace for building."""
import subprocess
import os
from pathlib import Path
from pineapple.state import PipelineState


def setup_node(state: PipelineState) -> dict:
    """Prepare workspace: check tools, create branch if needed."""
    project_name = state.get("project_name", "unnamed")
    branch = state.get("branch", "main")

    # Check available tools
    tools = {}
    for tool in ["python", "git", "pytest"]:
        try:
            result = subprocess.run(
                [tool, "--version"],
                capture_output=True, text=True, timeout=10
            )
            tools[tool] = result.stdout.strip() or result.stderr.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            tools[tool] = None

    # Check git status
    git_branch = None
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=10
        )
        git_branch = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    workspace_info = {
        "project_name": project_name,
        "branch": git_branch or branch,
        "tools_available": tools,
        "working_directory": str(Path.cwd()),
        "setup_complete": True,
    }

    print(f"[STAGE 4] Setup complete:")
    print(f"  Branch: {workspace_info['branch']}")
    print(f"  Tools: {', '.join(k for k, v in tools.items() if v)}")

    return {
        "current_stage": "setup",
        "workspace_info": workspace_info,
    }
