"""
apply_pipeline.py — Apply production pipeline templates to any project
======================================================================

Usage:
    py -3.12 tools/apply_pipeline.py <project-path> [--stack TYPE] [--dry-run]

Stacks:
    fastapi-vite   — FastAPI backend + Vite/React frontend (default)
    fastapi-only   — FastAPI backend only (API service)
    vite-only      — Static frontend only

What it does:
    1. Detects project structure (backend dir, frontend dir, ports)
    2. Fills template placeholders with detected values
    3. Copies filled templates to the project
    4. Creates .env.example (never overwrites .env)
    5. Adds .env to .gitignore if not already there
    6. Reports what was created/skipped

It NEVER overwrites existing files unless --force is passed.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def detect_project(project_path: Path) -> dict:
    """Auto-detect project configuration."""
    config = {
        "PROJECT_NAME": project_path.name,
        "PYTHON_VERSION": "3.12",
        "NODE_VERSION": "20",
        "BACKEND_DIR": "backend",
        "FRONTEND_DIR": "frontend",
        "BACKEND_PORT": "8000",
        "FRONTEND_PORT": "3000",
        "APP_MODULE": "app.main:app",
        "DB_PATH": "/data",
        "ENV_FILE": ".env",
        "TEST_COMMAND": "pytest",
        "API_URL": "http://backend:8000",
        "DEFAULT_LIMIT": "60/minute",
        "CHAT_LIMIT": "10/minute",
        "EXPORT_LIMIT": "5/minute",
        "EXTRA_SYSTEM_DEPS": "",
        "EXTRA_PIP_DEPS": "",
    }

    # Detect backend directory
    for candidate in ["backend", "server", "api", "src", "."]:
        if (project_path / candidate / "app").is_dir():
            config["BACKEND_DIR"] = candidate
            break
        if (project_path / candidate / "main.py").is_file():
            config["BACKEND_DIR"] = candidate
            break

    # Detect frontend directory
    for candidate in ["frontend", "client", "web", "ui"]:
        if (project_path / candidate / "package.json").is_file():
            config["FRONTEND_DIR"] = candidate
            break

    # Detect port from existing config
    backend_dir = project_path / config["BACKEND_DIR"]
    for config_file in ["app/config.py", "config.py", ".env"]:
        path = backend_dir / config_file
        if path.is_file():
            text = path.read_text(errors="ignore")
            port_match = re.search(r"(?:port|PORT)\s*[=:]\s*(\d{4,5})", text)
            if port_match:
                config["BACKEND_PORT"] = port_match.group(1)
                break

    # Detect FastAPI app module
    for module_path in [
        backend_dir / "app" / "main.py",
        backend_dir / "main.py",
        backend_dir / "server.py",
    ]:
        if module_path.is_file():
            text = module_path.read_text(errors="ignore")
            app_match = re.search(r"(\w+)\s*=\s*FastAPI\(", text)
            if app_match:
                rel = module_path.relative_to(backend_dir)
                module = str(rel).replace(os.sep, ".").replace(".py", "")
                config["APP_MODULE"] = f"{module}:{app_match.group(1)}"
                break

    # Detect test command
    if (backend_dir / "pyproject.toml").is_file():
        text = (backend_dir / "pyproject.toml").read_text(errors="ignore")
        if "pytest" in text:
            config["TEST_COMMAND"] = "pytest"

    return config


def fill_template(template_text: str, config: dict) -> str:
    """Replace {{PLACEHOLDER}} with config values."""
    result = template_text
    for key, value in config.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
    return result


def apply_file(
    template_name: str,
    dest_path: Path,
    config: dict,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Apply a single template to a destination."""
    template_path = TEMPLATE_DIR / template_name

    if not template_path.is_file():
        return f"  SKIP  {template_name} (template not found)"

    if dest_path.is_file() and not force:
        return f"  SKIP  {dest_path} (already exists, use --force to overwrite)"

    template_text = template_path.read_text(encoding="utf-8")
    filled = fill_template(template_text, config)

    if dry_run:
        return f"  WOULD CREATE  {dest_path}"

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(filled, encoding="utf-8")
    return f"  CREATED  {dest_path}"


def ensure_gitignore(project_path: Path, dry_run: bool = False) -> str:
    """Ensure .env is in .gitignore."""
    gitignore = project_path / ".gitignore"

    if gitignore.is_file():
        text = gitignore.read_text()
        if ".env" in text:
            return "  OK  .gitignore already has .env"

    if dry_run:
        return "  WOULD ADD  .env to .gitignore"

    with open(gitignore, "a") as f:
        f.write("\n# Environment variables (never commit)\n.env\n")
    return "  ADDED  .env to .gitignore"


def apply_pipeline(
    project_path: Path,
    stack: str = "fastapi-vite",
    force: bool = False,
    dry_run: bool = False,
):
    """Apply production pipeline templates to a project."""
    config = detect_project(project_path)

    print(f"\nProduction Pipeline — Applying to: {project_path}")
    print(f"  Stack: {stack}")
    print(f"  Detected: backend={config['BACKEND_DIR']}, "
          f"frontend={config['FRONTEND_DIR']}, "
          f"port={config['BACKEND_PORT']}")
    print(f"  App module: {config['APP_MODULE']}")
    print()

    results = []

    # Docker files
    if stack in ("fastapi-vite", "fastapi-only"):
        results.append(apply_file(
            "Dockerfile.fastapi",
            project_path / config["BACKEND_DIR"] / "Dockerfile",
            config, force, dry_run,
        ))

    if stack in ("fastapi-vite", "vite-only"):
        results.append(apply_file(
            "Dockerfile.vite",
            project_path / config["FRONTEND_DIR"] / "Dockerfile",
            config, force, dry_run,
        ))

    if stack == "fastapi-vite":
        results.append(apply_file(
            "docker-compose.template.yml",
            project_path / "docker-compose.yml",
            config, force, dry_run,
        ))

    # CI/CD
    results.append(apply_file(
        "ci.github-actions.yml",
        project_path / ".github" / "workflows" / "ci.yml",
        config, force, dry_run,
    ))

    # Environment
    results.append(apply_file(
        "env.template",
        project_path / ".env.example",
        config, force, dry_run,
    ))

    # Python templates (backend only)
    if stack in ("fastapi-vite", "fastapi-only"):
        backend = project_path / config["BACKEND_DIR"]

        results.append(apply_file(
            "rate_limiter.py",
            backend / "app" / "middleware" / "rate_limiter.py",
            config, force, dry_run,
        ))
        results.append(apply_file(
            "input_guardrails.py",
            backend / "app" / "middleware" / "input_guardrails.py",
            config, force, dry_run,
        ))
        results.append(apply_file(
            "observability.py",
            backend / "app" / "middleware" / "observability.py",
            config, force, dry_run,
        ))
        results.append(apply_file(
            "resilience.py",
            backend / "app" / "middleware" / "resilience.py",
            config, force, dry_run,
        ))

        # Cache + MCP (Phase 4 templates)
        results.append(apply_file(
            "cache.py",
            backend / "app" / "middleware" / "cache.py",
            config, force, dry_run,
        ))
        results.append(apply_file(
            "mcp_server.py",
            backend / "mcp_server.py",
            config, force, dry_run,
        ))

        # Test templates
        results.append(apply_file(
            "test_adversarial.py",
            backend / "tests" / "test_adversarial.py",
            config, force, dry_run,
        ))
        results.append(apply_file(
            "test_eval_benchmark.py",
            backend / "tests" / "test_eval_benchmark.py",
            config, force, dry_run,
        ))

    # Gitignore
    results.append(ensure_gitignore(project_path, dry_run))

    # Project scaffolding
    if stack in ("fastapi-vite", "fastapi-only"):
        mcp_json = project_path / ".mcp.json"
        if not mcp_json.is_file() or force:
            if not dry_run:
                mcp_json.parent.mkdir(parents=True, exist_ok=True)
                mcp_content = {
                    "mcpServers": {
                        f"{config['PROJECT_NAME']}-tools": {
                            "command": "python",
                            "args": ["mcp_server.py"],
                            "cwd": config["BACKEND_DIR"],
                        }
                    }
                }
                mcp_json.write_text(json.dumps(mcp_content, indent=2) + "\n")
                results.append(f"  CREATED  {mcp_json}")
            else:
                results.append(f"  WOULD CREATE  {mcp_json}")
        else:
            results.append(f"  SKIP  {mcp_json} (already exists)")

    claude_md = project_path / "CLAUDE.md"
    if not claude_md.is_file() or force:
        if not dry_run:
            claude_md.write_text(
                f"# {config['PROJECT_NAME']}\n\n"
                "## Validation\n"
                f"- Run tests: `cd {config['BACKEND_DIR']} && pytest -v`\n\n"
                "## Pipeline\n"
                "- This project uses the Pineapple Pipeline.\n"
                "- See `docs/superpowers/specs/` for design specs.\n"
                "- See `docs/superpowers/plans/` for implementation plans.\n"
            )
            results.append(f"  CREATED  {claude_md}")
        else:
            results.append(f"  WOULD CREATE  {claude_md}")
    else:
        results.append(f"  SKIP  {claude_md} (already exists)")

    pineapple_dir = project_path / ".pineapple"
    if not pineapple_dir.is_dir():
        if not dry_run:
            pineapple_dir.mkdir(parents=True, exist_ok=True)
            results.append(f"  CREATED  {pineapple_dir}/")
        else:
            results.append(f"  WOULD CREATE  {pineapple_dir}/")

    memory_dir = project_path / "memory"
    memory_md = memory_dir / "MEMORY.md"
    if not memory_md.is_file() or force:
        if not dry_run:
            memory_dir.mkdir(parents=True, exist_ok=True)
            memory_md.write_text(f"# {config['PROJECT_NAME']} Memory\n\n")
            results.append(f"  CREATED  {memory_md}")
        else:
            results.append(f"  WOULD CREATE  {memory_md}")

    projects_dir = project_path / "projects"
    bible_path = projects_dir / f"{config['PROJECT_NAME']}-bible.yaml"
    if not bible_path.is_file() or force:
        if not dry_run:
            projects_dir.mkdir(parents=True, exist_ok=True)
            bible_path.write_text(
                f"# {config['PROJECT_NAME']} Gap Tracker\n"
                "# Generated by Pineapple Pipeline\n\n"
                "summary:\n"
                "  total: 0\n"
                "  open: 0\n"
                "  closed: 0\n\n"
                "gaps: []\n"
            )
            results.append(f"  CREATED  {bible_path}")
        else:
            results.append(f"  WOULD CREATE  {bible_path}")

    # Report
    print("Results:")
    for r in results:
        print(r)

    print(f"\n{'DRY RUN — no files written' if dry_run else 'Done.'}")
    print(f"Next: review generated files, then run 'docker-compose up --build'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply production pipeline to a project")
    parser.add_argument("project_path", type=Path, help="Path to the project root")
    parser.add_argument("--stack", default="fastapi-vite",
                       choices=["fastapi-vite", "fastapi-only", "vite-only"],
                       help="Project stack type")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")

    args = parser.parse_args()

    if not args.project_path.is_dir():
        print(f"Error: {args.project_path} is not a directory")
        sys.exit(1)

    apply_pipeline(args.project_path, args.stack, args.force, args.dry_run)
