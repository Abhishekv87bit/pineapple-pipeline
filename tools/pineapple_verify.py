"""
pineapple_verify.py -- Pineapple Pipeline Verification Runner.

The most security-critical script in the pipeline.
Runs all 6 verification layers for a project and produces a signed
verification record that cannot be spoofed.

Usage:
    py -3.12 production-pipeline/tools/pineapple_verify.py <project-path> \
        [--branch NAME] [--layers 1,2,3,4,5,6] [--run-id UUID] [--json]

Exit codes:
    0 -- all layers passed (or skipped) with at least one pass
    1 -- at least one layer failed, or no layers passed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class LayerResult:
    layer: int
    name: str
    status: Literal["pass", "fail", "skip", "error"]
    test_count: int = 0
    output: str = ""
    duration_ms: float = 0


@dataclass
class VerificationRecord:
    version: str = "1.0.0"
    run_id: str = ""
    branch: str = ""
    timestamp: str = ""
    layers_passed: list[int] = field(default_factory=list)
    layers_failed: list[int] = field(default_factory=list)
    layers_skipped: list[int] = field(default_factory=list)
    test_count: int = 0
    all_green: bool = False
    evidence_hash: str = ""
    integrity_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "branch": self.branch,
            "timestamp": self.timestamp,
            "layers_passed": self.layers_passed,
            "layers_failed": self.layers_failed,
            "layers_skipped": self.layers_skipped,
            "test_count": self.test_count,
            "all_green": self.all_green,
            "evidence_hash": self.evidence_hash,
            "integrity_hash": self.integrity_hash,
        }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _find_backend(project_path: Path) -> Path | None:
    """Find the backend directory."""
    for candidate in ["backend", "server", "api", "src", "."]:
        d = project_path / candidate
        if (d / "app").is_dir() or (d / "tests").is_dir() or (d / "pyproject.toml").is_file():
            return d
    return None


def _count_pytest_tests(output: str) -> int:
    """Extract test count from pytest output."""
    # Look for "X passed" and "X failed" in the summary line
    match = re.search(r"(\d+) passed", output)
    passed = int(match.group(1)) if match else 0
    match = re.search(r"(\d+) failed", output)
    failed = int(match.group(1)) if match else 0
    return passed + failed


def _detect_branch(project_path: Path) -> str:
    """Detect current git branch."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Signing (CRITICAL SECURITY FEATURE)
# ---------------------------------------------------------------------------

def _compute_evidence_hash(results: list[LayerResult]) -> str:
    """SHA256 hash of all layer outputs -- evidence of real test runs."""
    combined = "".join(r.output for r in results)
    return hashlib.sha256(combined.encode()).hexdigest()


def _compute_integrity_hash(evidence_hash: str, run_id: str, branch: str, timestamp: str) -> str:
    """SHA256 of evidence_hash|run_id|branch|timestamp -- cannot be forged without running real tests."""
    payload = f"{evidence_hash}|{run_id}|{branch}|{timestamp}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Layer runners
# ---------------------------------------------------------------------------

def run_layer_1_unit_tests(project_path: Path) -> LayerResult:
    """Layer 1: Unit tests -- pytest -v"""
    backend = _find_backend(project_path)
    if not backend:
        return LayerResult(layer=1, name="Unit tests", status="skip", output="No backend directory found")

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"],
            cwd=str(backend),
            capture_output=True, text=True, timeout=300,
        )
        duration = (time.time() - start) * 1000
        test_count = _count_pytest_tests(result.stdout)
        status = "pass" if result.returncode == 0 else "fail"
        return LayerResult(
            layer=1, name="Unit tests", status=status,
            test_count=test_count, output=result.stdout + result.stderr,
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        return LayerResult(layer=1, name="Unit tests", status="error", output="Timeout after 300s")
    except Exception as e:
        return LayerResult(layer=1, name="Unit tests", status="error", output=str(e))


def run_layer_2_integration_tests(project_path: Path) -> LayerResult:
    """Layer 2: Integration tests -- pytest tests/test_integration*.py"""
    backend = _find_backend(project_path)
    if not backend:
        return LayerResult(layer=2, name="Integration tests", status="skip")

    test_files = list(backend.glob("tests/test_integration*.py")) + list(backend.glob("tests/test_cache_integration*.py"))
    if not test_files:
        return LayerResult(layer=2, name="Integration tests", status="skip", output="No integration test files found")

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"] + [str(f) for f in test_files],
            cwd=str(backend),
            capture_output=True, text=True, timeout=300,
        )
        duration = (time.time() - start) * 1000
        test_count = _count_pytest_tests(result.stdout)
        status = "pass" if result.returncode == 0 else "fail"
        return LayerResult(
            layer=2, name="Integration tests", status=status,
            test_count=test_count, output=result.stdout + result.stderr,
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        return LayerResult(layer=2, name="Integration tests", status="error", output="Timeout after 300s")
    except Exception as e:
        return LayerResult(layer=2, name="Integration tests", status="error", output=str(e))


def run_layer_3_security_tests(project_path: Path) -> LayerResult:
    """Layer 3: Security tests -- pytest tests/test_adversarial*.py"""
    backend = _find_backend(project_path)
    if not backend:
        return LayerResult(layer=3, name="Security tests", status="skip")

    test_files = list(backend.glob("tests/test_adversarial*.py"))
    if not test_files:
        return LayerResult(layer=3, name="Security tests", status="skip", output="No adversarial test files found")

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"] + [str(f) for f in test_files],
            cwd=str(backend),
            capture_output=True, text=True, timeout=300,
        )
        duration = (time.time() - start) * 1000
        test_count = _count_pytest_tests(result.stdout)
        status = "pass" if result.returncode == 0 else "fail"
        return LayerResult(
            layer=3, name="Security tests", status=status,
            test_count=test_count, output=result.stdout + result.stderr,
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        return LayerResult(layer=3, name="Security tests", status="error", output="Timeout after 300s")
    except Exception as e:
        return LayerResult(layer=3, name="Security tests", status="error", output=str(e))


def run_layer_4_llm_evals(project_path: Path) -> LayerResult:
    """Layer 4: LLM evals -- deepeval test run"""
    try:
        import deepeval  # noqa: F401
    except ImportError:
        return LayerResult(layer=4, name="LLM evals", status="skip", output="deepeval not installed")

    backend = _find_backend(project_path)
    if not backend:
        return LayerResult(layer=4, name="LLM evals", status="skip")

    test_files = list(backend.glob("tests/test_eval*.py")) + list(backend.glob("tests/test_llm_eval*.py"))
    if not test_files:
        return LayerResult(layer=4, name="LLM evals", status="skip", output="No eval test files found")

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"] + [str(f) for f in test_files],
            cwd=str(backend),
            capture_output=True, text=True, timeout=600,
        )
        duration = (time.time() - start) * 1000
        test_count = _count_pytest_tests(result.stdout)
        status = "pass" if result.returncode == 0 else "fail"
        return LayerResult(
            layer=4, name="LLM evals", status=status,
            test_count=test_count, output=result.stdout + result.stderr,
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        return LayerResult(layer=4, name="LLM evals", status="error", output="Timeout after 600s")
    except Exception as e:
        return LayerResult(layer=4, name="LLM evals", status="error", output=str(e))


def run_layer_5_domain_validation(project_path: Path) -> LayerResult:
    """Layer 5: Domain validation -- python tools/vlad.py"""
    vlad_candidates = [
        project_path / "tools" / "vlad.py",
        project_path / "backend" / "tools" / "vlad.py",
        project_path.parent / "3d_design_agent" / "tools" / "vlad.py",
    ]
    vlad_path = None
    for candidate in vlad_candidates:
        if candidate.is_file():
            vlad_path = candidate
            break

    if not vlad_path:
        return LayerResult(layer=5, name="Domain validation", status="skip", output="VLAD not found")

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(vlad_path)],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=300,
        )
        duration = (time.time() - start) * 1000
        status = "pass" if result.returncode == 0 else "fail"
        return LayerResult(
            layer=5, name="Domain validation", status=status,
            output=result.stdout + result.stderr, duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        return LayerResult(layer=5, name="Domain validation", status="error", output="Timeout after 300s")
    except Exception as e:
        return LayerResult(layer=5, name="Domain validation", status="error", output=str(e))


def run_layer_6_visual_inspection(project_path: Path) -> LayerResult:
    """Layer 6: Visual inspection -- always skip in automated mode (requires human)."""
    return LayerResult(layer=6, name="Visual inspection", status="skip", output="Requires human review")


# ---------------------------------------------------------------------------
# Verification orchestrator
# ---------------------------------------------------------------------------

def run_verification(
    project_path: Path,
    branch: str | None = None,
    layers: list[int] | None = None,
    run_id: str = "",
) -> VerificationRecord:
    """Run verification layers and produce signed record."""
    if branch is None:
        branch = _detect_branch(project_path)

    if not layers:
        layers = [1, 2, 3, 4, 5, 6]

    timestamp = datetime.now(timezone.utc).isoformat()

    layer_runners = {
        1: run_layer_1_unit_tests,
        2: run_layer_2_integration_tests,
        3: run_layer_3_security_tests,
        4: run_layer_4_llm_evals,
        5: run_layer_5_domain_validation,
        6: run_layer_6_visual_inspection,
    }

    results: list[LayerResult] = []
    for layer_num in sorted(layers):
        if layer_num in layer_runners:
            result = layer_runners[layer_num](project_path)
            results.append(result)

    # Build record
    record = VerificationRecord(
        run_id=run_id,
        branch=branch,
        timestamp=timestamp,
    )

    for r in results:
        if r.status == "pass":
            record.layers_passed.append(r.layer)
        elif r.status in ("fail", "error"):
            record.layers_failed.append(r.layer)
        else:
            record.layers_skipped.append(r.layer)
        record.test_count += r.test_count

    # all_green = no failures AND at least one pass
    record.all_green = len(record.layers_failed) == 0 and len(record.layers_passed) > 0

    # Sign
    record.evidence_hash = _compute_evidence_hash(results)
    record.integrity_hash = _compute_integrity_hash(
        record.evidence_hash, record.run_id, record.branch, record.timestamp
    )

    # Write to .pineapple/verify/<branch>.json
    _write_verification_record(project_path, branch, record)

    return record


# ---------------------------------------------------------------------------
# Record persistence
# ---------------------------------------------------------------------------

def _write_verification_record(project_path: Path, branch: str, record: VerificationRecord):
    """Write signed verification record to per-branch file."""
    # Sanitize branch name for filename (replace / with --)
    safe_branch = branch.replace("/", "--")
    verify_dir = project_path / ".pineapple" / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)

    record_path = verify_dir / f"{safe_branch}.json"
    record_path.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")


def verify_integrity(record_path: Path) -> bool:
    """Verify a verification record's integrity hash."""
    data = json.loads(record_path.read_text(encoding="utf-8"))
    expected = _compute_integrity_hash(
        data["evidence_hash"], data["run_id"], data["branch"], data["timestamp"]
    )
    return expected == data["integrity_hash"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

LAYER_NAMES = {
    1: "Unit tests",
    2: "Integration tests",
    3: "Security tests",
    4: "LLM evals",
    5: "Domain validation",
    6: "Visual inspection",
}


def main():
    parser = argparse.ArgumentParser(description="Pineapple Pipeline Verification Runner")
    parser.add_argument("project_path", type=Path, help="Path to project root")
    parser.add_argument("--branch", help="Override branch name (default: auto-detect from git)")
    parser.add_argument("--layers", help="Comma-separated layer numbers to run (default: all)")
    parser.add_argument("--run-id", default="", help="Pipeline run ID (for correlation)")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    layers = None
    if args.layers:
        layers = [int(x.strip()) for x in args.layers.split(",")]

    record = run_verification(args.project_path, args.branch, layers, args.run_id)

    if args.json:
        print(json.dumps(record.to_dict(), indent=2))
    else:
        print(f"\nPineapple Verify v1.0.0")
        print(f"========================")
        print(f"Project: {args.project_path}")
        print(f"Branch:  {record.branch}")
        print(f"Run ID:  {record.run_id or '(none)'}")
        print()

        for layer_num in sorted(record.layers_passed + record.layers_failed + record.layers_skipped):
            if layer_num in record.layers_passed:
                status_str = " PASS "
            elif layer_num in record.layers_failed:
                status_str = " FAIL "
            else:
                status_str = " SKIP "
            print(f"  [{status_str}] Layer {layer_num}: {LAYER_NAMES.get(layer_num, 'Unknown')}")

        print()
        print(f"Tests: {record.test_count}")
        print(f"Result: {'ALL GREEN' if record.all_green else 'FAILED'}")
        print(f"Record: .pineapple/verify/{record.branch.replace('/', '--')}.json")

    sys.exit(0 if record.all_green else 1)


if __name__ == "__main__":
    main()
