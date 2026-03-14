"""
pineapple_doctor.py -- Health check tool for the Pineapple Pipeline.

Verifies all shared services and dependencies are working. This is the first
thing a user runs to ensure their environment is ready.

Usage:
    py -3.12 production-pipeline/tools/pineapple_doctor.py [--json]

Exit codes:
    0 -- all required checks pass (optional skips are fine)
    1 -- at least one required check failed
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import Literal

logger = logging.getLogger("pineapple.doctor")

VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "fail", "skip"]
    message: str
    required: bool = True  # If required and fail -> overall fail


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def overall_pass(self) -> bool:
        return all(c.status != "fail" for c in self.checks if c.required)

    def to_dict(self) -> dict:
        return {
            "overall": "pass" if self.overall_pass else "fail",
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "required": c.required,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# HTTP helper (httpx preferred, stdlib fallback)
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: float = 5.0) -> int:
    """HTTP GET returning the status code. Raises on connection failure."""
    try:
        import httpx  # type: ignore[import-untyped]
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return resp.status_code
    except ImportError:
        pass

    import urllib.request
    import urllib.error
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.getcode()


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_docker() -> CheckResult:
    """Verify Docker CLI is available and the daemon is responsive."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return CheckResult(
                name="docker",
                status="pass",
                message="Docker daemon running",
                required=True,
            )
        return CheckResult(
            name="docker",
            status="fail",
            message="Docker not found or not running",
            required=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return CheckResult(
            name="docker",
            status="fail",
            message="Docker not found or not running",
            required=True,
        )


def check_langfuse() -> CheckResult:
    """HTTP GET to LangFuse health endpoint."""
    try:
        status = _http_get("http://localhost:3000/api/public/health", timeout=5.0)
        if status < 400:
            return CheckResult(
                name="langfuse",
                status="pass",
                message="LangFuse reachable at localhost:3000",
                required=False,
            )
        return CheckResult(
            name="langfuse",
            status="skip",
            message="LangFuse not running (optional)",
            required=False,
        )
    except Exception:
        return CheckResult(
            name="langfuse",
            status="skip",
            message="LangFuse not running (optional)",
            required=False,
        )


def check_mem0() -> CheckResult:
    """HTTP GET to Mem0 health endpoint."""
    try:
        status = _http_get("http://localhost:8080/health", timeout=5.0)
        if status < 400:
            return CheckResult(
                name="mem0",
                status="pass",
                message="Mem0 API responding",
                required=False,
            )
        return CheckResult(
            name="mem0",
            status="skip",
            message="Mem0 not running (optional)",
            required=False,
        )
    except Exception:
        return CheckResult(
            name="mem0",
            status="skip",
            message="Mem0 not running (optional)",
            required=False,
        )


def check_neo4j() -> CheckResult:
    """Socket connect to Neo4j bolt port."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(("localhost", 7687))
        sock.close()
        return CheckResult(
            name="neo4j",
            status="pass",
            message="Neo4j bolt port reachable",
            required=False,
        )
    except (OSError, socket.timeout):
        return CheckResult(
            name="neo4j",
            status="skip",
            message="Neo4j not running (optional)",
            required=False,
        )


def check_python_package(name: str, *, required: bool = False) -> CheckResult:
    """Try to import a Python package by name."""
    try:
        importlib.import_module(name)
        return CheckResult(
            name=name,
            status="pass",
            message=f"{name} installed",
            required=required,
        )
    except ImportError:
        if required:
            return CheckResult(
                name=name,
                status="fail",
                message=f"{name} not installed (required)",
                required=True,
            )
        return CheckResult(
            name=name,
            status="skip",
            message=f"{name} not installed (optional)",
            required=False,
        )


def check_hookify_rules() -> CheckResult:
    """Glob ~/.claude/hookify.*.local.md, count files."""
    pattern = str(Path.home() / ".claude" / "hookify.*.local.md")
    files = glob(pattern)
    count = len(files)
    if count >= 11:
        return CheckResult(
            name="hookify_rules",
            status="pass",
            message=f"{count} hookify rules found",
            required=True,
        )
    return CheckResult(
        name="hookify_rules",
        status="fail",
        message=f"Only {count} hookify rules (expected >= 11)",
        required=True,
    )


def check_templates() -> CheckResult:
    """Count files in production-pipeline/templates/, expect >= 11."""
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    if not templates_dir.is_dir():
        return CheckResult(
            name="templates",
            status="fail",
            message=f"Templates directory not found: {templates_dir}",
            required=True,
        )
    count = sum(1 for f in templates_dir.iterdir() if f.is_file())
    if count >= 11:
        return CheckResult(
            name="templates",
            status="pass",
            message=f"{count} templates found",
            required=True,
        )
    return CheckResult(
        name="templates",
        status="fail",
        message=f"Only {count} templates (expected >= 11)",
        required=True,
    )


def check_config() -> CheckResult:
    """Try to import and load PineappleConfig, run validate."""
    try:
        # Add tools/ to sys.path so sibling module resolves
        tools_dir = str(Path(__file__).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        from pineapple_config import load_config, validate_config  # type: ignore[import-untyped]

        cfg = load_config()
        warns = validate_config(cfg)
        if warns:
            return CheckResult(
                name="config",
                status="pass",
                message=f"Config valid with {len(warns)} warnings",
                required=True,
            )
        return CheckResult(
            name="config",
            status="pass",
            message="Config valid",
            required=True,
        )
    except Exception as exc:
        return CheckResult(
            name="config",
            status="fail",
            message=f"Config failed to load: {exc}",
            required=True,
        )


def check_pipeline_tools() -> CheckResult:
    """Verify core tools exist on disk."""
    tools_dir = Path(__file__).resolve().parent
    expected = ["apply_pipeline.py", "pipeline_state.py", "pineapple_config.py"]
    missing = [name for name in expected if not (tools_dir / name).is_file()]
    if not missing:
        return CheckResult(
            name="pipeline_tools",
            status="pass",
            message="All pipeline tools present",
            required=True,
        )
    return CheckResult(
        name="pipeline_tools",
        status="fail",
        message=f"Missing tools: {', '.join(missing)}",
        required=True,
    )


def check_pydantic() -> CheckResult:
    """Try to import pydantic."""
    try:
        importlib.import_module("pydantic")
        return CheckResult(
            name="pydantic",
            status="pass",
            message="pydantic installed",
            required=True,
        )
    except ImportError:
        return CheckResult(
            name="pydantic",
            status="fail",
            message="pydantic not installed (required)",
            required=True,
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_doctor() -> DoctorReport:
    report = DoctorReport()
    report.checks.append(check_docker())
    report.checks.append(check_langfuse())
    report.checks.append(check_mem0())
    report.checks.append(check_neo4j())
    report.checks.append(check_python_package("deepeval", required=False))
    report.checks.append(check_python_package("dspy", required=False))
    report.checks.append(check_hookify_rules())
    report.checks.append(check_templates())
    report.checks.append(check_config())
    report.checks.append(check_pipeline_tools())
    report.checks.append(check_pydantic())
    return report


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _use_color() -> bool:
    """Decide whether to emit ANSI color codes."""
    # On Windows, only use color if TERM is set (e.g., Windows Terminal, Git Bash)
    if sys.platform == "win32" and not os.getenv("TERM"):
        return False
    # Respect NO_COLOR convention
    if os.getenv("NO_COLOR"):
        return False
    # Only color if stdout is a real terminal
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _format_status(status: str, color: bool) -> str:
    """Return a fixed-width status label, optionally colorized."""
    if color:
        codes = {"pass": "\033[32m", "fail": "\033[31m", "skip": "\033[33m"}
        reset = "\033[0m"
        code = codes.get(status, "")
        return f"{code} {status.upper()} {reset}"
    return f" {status.upper()} "


def print_report(report: DoctorReport) -> None:
    """Print a human-readable report to stdout."""
    color = _use_color()

    print(f"Pineapple Doctor v{VERSION}")
    print("=" * 40)

    for check in report.checks:
        tag = _format_status(check.status, color)
        print(f"{tag} {check.message}")

    print("=" * 40)

    total = len(report.checks)
    passed = sum(1 for c in report.checks if c.status == "pass")
    skipped = sum(1 for c in report.checks if c.status == "skip")
    failed = sum(1 for c in report.checks if c.status == "fail")

    overall_label = "PASS" if report.overall_pass else "FAIL"
    parts = [f"{passed}/{total} passed"]
    if skipped:
        parts.append(f"{skipped} optional skipped")
    if failed:
        parts.append(f"{failed} failed")

    if color:
        overall_code = "\033[32m" if report.overall_pass else "\033[31m"
        reset = "\033[0m"
        print(f"Overall: {overall_code}{overall_label}{reset} ({', '.join(parts)})")
    else:
        print(f"Overall: {overall_label} ({', '.join(parts)})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    json_mode = "--json" in sys.argv

    report = run_doctor()

    if json_mode:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_report(report)

    sys.exit(0 if report.overall_pass else 1)


if __name__ == "__main__":
    main()
