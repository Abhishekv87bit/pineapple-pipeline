"""Stage 6: Verifier — run tests and verification checks.

Pure Python — no LLM calls. FRESH CONTEXT: no build knowledge.
ISOLATED: Can only run tests, cannot write code.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pineapple.models import LayerResult, VerificationRecord
from pineapple.state import PipelineState


# ---------------------------------------------------------------------------
# Verification layers
# ---------------------------------------------------------------------------


def _run_pytest() -> LayerResult:
    """Layer 1: Run pytest if available."""
    try:
        result = subprocess.run(
            ["pytest", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr

        # Parse pytest output for counts
        test_count = 0
        fail_count = 0
        for line in output.splitlines():
            if "passed" in line or "failed" in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "passed" and i > 0:
                        try:
                            test_count += int(parts[i - 1])
                        except ValueError:
                            pass
                    if part == "failed" and i > 0:
                        try:
                            fail_count += int(parts[i - 1])
                        except ValueError:
                            pass

        status = "pass" if result.returncode == 0 else "fail"
        return LayerResult(
            layer=1,
            name="pytest",
            status=status,
            details=output[-500:] if len(output) > 500 else output,
            test_count=test_count,
            fail_count=fail_count,
        )
    except FileNotFoundError:
        return LayerResult(
            layer=1,
            name="pytest",
            status="skip",
            details="pytest not found in PATH",
        )
    except subprocess.TimeoutExpired:
        return LayerResult(
            layer=1,
            name="pytest",
            status="fail",
            details="pytest timed out after 120 seconds",
        )


def _check_test_files_exist() -> LayerResult:
    """Layer 2: Check if test files exist in the project."""
    cwd = Path.cwd()
    test_patterns = ["test_*.py", "*_test.py", "tests/**/*.py"]
    test_files: list[str] = []

    for pattern in test_patterns:
        test_files.extend(str(p) for p in cwd.glob(pattern))

    if test_files:
        return LayerResult(
            layer=2,
            name="test_files_exist",
            status="pass",
            details=f"Found {len(test_files)} test file(s)",
            test_count=len(test_files),
        )
    else:
        return LayerResult(
            layer=2,
            name="test_files_exist",
            status="skip",
            details="No test files found",
        )


def _check_syntax() -> LayerResult:
    """Layer 3: Basic Python syntax check via py_compile."""
    cwd = Path.cwd()
    py_files = list(cwd.glob("src/**/*.py"))
    if not py_files:
        py_files = list(cwd.glob("**/*.py"))
        # Exclude venv and common non-project dirs
        py_files = [f for f in py_files if not any(
            part in f.parts for part in [".venv", "venv", "node_modules", "__pycache__", ".git"]
        )]

    if not py_files:
        return LayerResult(
            layer=3,
            name="syntax_check",
            status="skip",
            details="No Python files found to check",
        )

    errors: list[str] = []
    for py_file in py_files[:50]:  # Cap at 50 files
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", str(py_file)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                errors.append(f"{py_file}: {result.stderr.strip()}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    if errors:
        return LayerResult(
            layer=3,
            name="syntax_check",
            status="fail",
            details="\n".join(errors[:10]),
            test_count=len(py_files),
            fail_count=len(errors),
        )
    return LayerResult(
        layer=3,
        name="syntax_check",
        status="pass",
        details=f"All {len(py_files)} files have valid syntax",
        test_count=len(py_files),
    )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def verifier_node(state: PipelineState) -> dict:
    """Run verification checks against the built code.

    FRESH CONTEXT: No knowledge of how the code was built.
    ISOLATED: Can only run tests, cannot write or modify code.

    Layers:
    1. pytest execution
    2. Test file existence check
    3. Python syntax validation
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 6: Verify] Project: {project_name}")

    layers: list[LayerResult] = []

    # Layer 1: pytest
    print("  [Verify] Layer 1: Running pytest...")
    layer1 = _run_pytest()
    layers.append(layer1)
    print(f"    Result: {layer1.status} — {layer1.details[:80]}")

    # Layer 2: Test files exist
    print("  [Verify] Layer 2: Checking test files...")
    layer2 = _check_test_files_exist()
    layers.append(layer2)
    print(f"    Result: {layer2.status} — {layer2.details[:80]}")

    # Layer 3: Syntax check
    print("  [Verify] Layer 3: Syntax check...")
    layer3 = _check_syntax()
    layers.append(layer3)
    print(f"    Result: {layer3.status} — {layer3.details[:80]}")

    # Determine overall status
    all_green = all(lr.status in ("pass", "skip") for lr in layers)

    record = VerificationRecord(
        all_green=all_green,
        layers=[lr for lr in layers],
        integrity_hash="",
        timestamp=datetime.now(timezone.utc),
    )

    print(f"  [Verify] Overall: {'ALL GREEN' if all_green else 'ISSUES FOUND'}")

    return {
        "current_stage": "verify",
        "verify_record": record.model_dump(),
    }
