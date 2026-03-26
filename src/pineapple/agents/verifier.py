"""Stage 6: Verifier — run tests and verification checks.

Pure Python — no LLM calls. FRESH CONTEXT: no build knowledge.
ISOLATED: Can only run tests, cannot write code.
"""

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pineapple.models import LayerResult, VerificationRecord
from pineapple.state import PipelineState


# ---------------------------------------------------------------------------
# Verification layers
# ---------------------------------------------------------------------------


def _run_pytest(workspace: Optional[str] = None) -> LayerResult:
    """Layer 1: Run pytest if available."""
    try:
        result = subprocess.run(
            ["pytest", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=workspace,
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


def _check_test_files_exist(workspace: Optional[str] = None) -> LayerResult:
    """Layer 2: Check if test files exist in the project."""
    cwd = Path(workspace) if workspace else Path.cwd()
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


def _check_syntax(workspace: Optional[str] = None) -> LayerResult:
    """Layer 3: Basic Python syntax check via py_compile."""
    cwd = Path(workspace) if workspace else Path.cwd()
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


def _run_security_scan(workspace: Optional[str] = None) -> LayerResult:
    """Layer 4: Security scan via bandit + pattern matching."""
    findings: list[str] = []
    cwd = Path(workspace) if workspace else Path.cwd()

    # Try bandit first
    bandit_available = False
    try:
        result = subprocess.run(
            ["bandit", "-r", "src", "-f", "json", "-q"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(cwd),
        )
        bandit_available = True
        if result.returncode != 0 and result.stdout.strip():
            # bandit returns non-zero when it finds issues
            import json as _json
            try:
                report = _json.loads(result.stdout)
                for issue in report.get("results", [])[:10]:
                    sev = issue.get("issue_severity", "?")
                    msg = issue.get("issue_text", "unknown")
                    fname = issue.get("filename", "?")
                    line = issue.get("line_number", "?")
                    findings.append(f"[{sev}] {fname}:{line} — {msg}")
            except (ValueError, KeyError):
                # JSON parse failed; treat raw output as finding
                findings.append(result.stdout[:200])
    except FileNotFoundError:
        pass  # bandit not installed — fall through to pattern scan
    except subprocess.TimeoutExpired:
        findings.append("bandit timed out after 60s")

    # Fallback / supplement: regex pattern scan for common issues
    py_files = list(cwd.glob("src/**/*.py"))
    if not py_files:
        py_files = list(cwd.glob("**/*.py"))
        py_files = [f for f in py_files if not any(
            part in f.parts for part in [".venv", "venv", "node_modules", "__pycache__", ".git"]
        )]

    dangerous_patterns = [
        (re.compile(r'\beval\s*\('), "eval() usage"),
        (re.compile(r'\bexec\s*\('), "exec() usage"),
        (re.compile(r'(?i)(password|secret|api_key|token)\s*=\s*["\'][^"\']+["\']'), "hardcoded secret"),
        (re.compile(r'pickle\.loads?\s*\('), "pickle deserialization"),
        (re.compile(r'subprocess\..*shell\s*=\s*True'), "shell=True in subprocess"),
    ]

    pattern_hits: list[str] = []
    for py_file in py_files[:50]:
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), 1):
                for pat, label in dangerous_patterns:
                    if pat.search(line):
                        pattern_hits.append(f"{py_file.name}:{lineno} — {label}")
        except OSError:
            pass

    findings.extend(pattern_hits[:20])

    if findings:
        return LayerResult(
            layer=4,
            name="security_scan",
            status="fail",
            details="\n".join(findings[:15]),
            test_count=len(py_files),
            fail_count=len(findings),
        )

    source = "bandit + patterns" if bandit_available else "pattern scan only"
    return LayerResult(
        layer=4,
        name="security_scan",
        status="pass",
        details=f"No security issues found ({source}, {len(py_files)} files)",
        test_count=len(py_files),
    )


def _run_code_quality(workspace: Optional[str] = None) -> LayerResult:
    """Layer 5: Code quality via ruff or flake8."""
    run_cwd = workspace or None
    # Try ruff first (faster, modern)
    for tool_name, cmd in [
        ("ruff", ["ruff", "check", "src", "--select", "F,E,W", "--no-fix", "--output-format", "text"]),
        ("flake8", ["flake8", "src", "--select", "F,E,W", "--max-line-length", "120"]),
    ]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=run_cwd,
            )
            output = result.stdout.strip()
            if result.returncode == 0:
                return LayerResult(
                    layer=5,
                    name="code_quality",
                    status="pass",
                    details=f"No issues found ({tool_name})",
                )

            # Non-zero means issues found
            lines = output.splitlines()
            issue_count = len(lines)
            truncated = "\n".join(lines[:15])
            if issue_count > 15:
                truncated += f"\n... and {issue_count - 15} more"

            return LayerResult(
                layer=5,
                name="code_quality",
                status="fail",
                details=truncated,
                test_count=issue_count,
                fail_count=issue_count,
            )
        except FileNotFoundError:
            continue  # Try next tool
        except subprocess.TimeoutExpired:
            return LayerResult(
                layer=5,
                name="code_quality",
                status="fail",
                details=f"{tool_name} timed out after 60s",
            )

    # Neither tool available
    return LayerResult(
        layer=5,
        name="code_quality",
        status="skip",
        details="Neither ruff nor flake8 found in PATH",
    )


def _run_deepeval(
    workspace: Optional[str] = None,
    build_results: list = None,
    design_spec: dict = None,
) -> LayerResult:
    """Layer 7: LLM quality evaluation via DeepEval."""
    try:
        from deepeval.metrics import GEval, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
    except ImportError:
        return LayerResult(
            layer=7,
            name="deepeval_quality",
            status="skip",
            details="deepeval not installed. Install with: pip install 'pineapple-pipeline[eval]'",
        )

    if not build_results or not design_spec:
        return LayerResult(
            layer=7,
            name="deepeval_quality",
            status="skip",
            details="No build results or design spec available for quality evaluation",
        )

    # Build the test case from pipeline artifacts
    spec_summary = design_spec.get("summary", "No design spec summary")
    code_output = "\n".join(
        str(r.get("files_written", [])) for r in build_results if isinstance(r, dict)
    )[:5000]  # Cap at 5k chars

    if not code_output.strip() or code_output.strip() == "[]":
        return LayerResult(
            layer=7,
            name="deepeval_quality",
            status="skip",
            details="No code output in build results to evaluate",
        )

    test_case = LLMTestCase(
        input=f"Implement the following design:\n{spec_summary}",
        actual_output=code_output,
        expected_output=spec_summary,
        retrieval_context=[spec_summary],
    )

    scores = {}
    failures = []

    # GEval: general quality
    try:
        geval = GEval(
            name="code_implements_spec",
            criteria="Does the generated code correctly implement the design specification? Consider completeness, correctness, and adherence to the spec.",
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.EXPECTED_OUTPUT,
            ],
            threshold=0.7,
        )
        geval.measure(test_case)
        scores["geval"] = geval.score
        if geval.score < 0.7:
            failures.append(f"GEval score {geval.score:.2f} < 0.7 threshold")
    except Exception as exc:
        scores["geval"] = None
        failures.append(f"GEval failed: {exc}")

    # Faithfulness: no hallucinated features
    try:
        faithfulness = FaithfulnessMetric(threshold=0.7)
        faithfulness.measure(test_case)
        scores["faithfulness"] = faithfulness.score
        if faithfulness.score < 0.7:
            failures.append(f"Faithfulness score {faithfulness.score:.2f} < 0.7 threshold")
    except Exception as exc:
        scores["faithfulness"] = None
        failures.append(f"Faithfulness failed: {exc}")

    score_summary = ", ".join(
        f"{k}={v:.2f}" if v is not None else f"{k}=N/A"
        for k, v in scores.items()
    )

    if failures:
        return LayerResult(
            layer=7,
            name="deepeval_quality",
            status="fail",
            details=f"Quality gates failed: {'; '.join(failures)}. Scores: {score_summary}",
            test_count=len(scores),
            fail_count=len(failures),
        )

    return LayerResult(
        layer=7,
        name="deepeval_quality",
        status="pass",
        details=f"All quality gates passed. Scores: {score_summary}",
        test_count=len(scores),
    )


def _run_domain_validation(workspace: Optional[str] = None) -> LayerResult:
    """Layer 6: Domain-specific validation for Pineapple Pipeline."""
    cwd = Path(workspace) if workspace else Path.cwd()
    issues: list[str] = []
    checks_run = 0

    # Check 1: Pydantic models import cleanly
    checks_run += 1
    try:
        result = subprocess.run(
            ["python", "-c", "from pineapple.models import *; print('OK')"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(cwd),
        )
        if result.returncode != 0:
            issues.append(f"Model import failed: {result.stderr.strip()[:150]}")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        issues.append(f"Model import check error: {exc}")

    # Check 2: All agent modules import cleanly
    checks_run += 1
    agent_modules = [
        "pineapple.agents.intake",
        "pineapple.agents.strategic_review",
        "pineapple.agents.architecture",
        "pineapple.agents.planner",
        "pineapple.agents.builder",
        "pineapple.agents.verifier",
        "pineapple.agents.reviewer",
        "pineapple.agents.shipper",
        "pineapple.agents.evolver",
    ]
    for mod in agent_modules:
        try:
            result = subprocess.run(
                ["python", "-c", f"import {mod}"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(cwd),
            )
            if result.returncode != 0:
                issues.append(f"Import {mod} failed: {result.stderr.strip()[:100]}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Check 3: Anti-pattern detection from dogfood lessons
    checks_run += 1
    agent_files = list(cwd.glob("src/pineapple/agents/*.py"))
    anti_patterns = [
        (re.compile(r'from pineapple\.agents\.builder\b'), "verifier.py", "verifier imports builder (isolation violation)"),
        (re.compile(r'from pineapple\.agents\.verifier\b'), "builder.py", "builder imports verifier (isolation violation)"),
        (re.compile(r'from pineapple\.agents\.(builder|verifier)\b'), "reviewer.py", "reviewer imports builder/verifier (isolation violation)"),
    ]
    for agent_file in agent_files:
        try:
            text = agent_file.read_text(encoding="utf-8", errors="ignore")
            for pat, target_name, label in anti_patterns:
                if target_name in agent_file.name and pat.search(text):
                    issues.append(label)
        except OSError:
            pass

    # Check 4: graph.py references all expected nodes
    checks_run += 1
    graph_file = cwd / "src" / "pineapple" / "graph.py"
    if graph_file.exists():
        try:
            graph_text = graph_file.read_text(encoding="utf-8", errors="ignore")
            expected_nodes = ["intake", "strategic_review", "architecture", "plan", "build", "verify", "review", "ship", "evolve"]
            for node in expected_nodes:
                if node not in graph_text:
                    issues.append(f"graph.py missing reference to '{node}' node")
        except OSError:
            pass

    if issues:
        return LayerResult(
            layer=6,
            name="domain_validation",
            status="fail",
            details="\n".join(issues[:15]),
            test_count=checks_run,
            fail_count=len(issues),
        )

    return LayerResult(
        layer=6,
        name="domain_validation",
        status="pass",
        details=f"All {checks_run} domain checks passed",
        test_count=checks_run,
    )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _get_branch(workspace: str | None) -> str:
    """Get current git branch name, or 'unknown' if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=workspace,
        )
        if result.returncode == 0:
            return result.stdout.strip().replace("/", "-")  # sanitize for filename
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _persist_record(record: VerificationRecord, workspace: str | None) -> None:
    """Write verification record to .pineapple/verify/<branch>.json and last_verify.json."""
    base = Path(workspace) if workspace else Path.cwd()
    verify_dir = base / ".pineapple" / "verify"
    try:
        verify_dir.mkdir(parents=True, exist_ok=True)
        record_data = json.dumps(record.model_dump(), indent=2, default=str)

        # Per-branch record
        branch = _get_branch(workspace)
        (verify_dir / f"{branch}.json").write_text(record_data, encoding="utf-8")

        # Convenience pointer
        (verify_dir / "last_verify.json").write_text(record_data, encoding="utf-8")

        print(f"  [Verify] Records written: .pineapple/verify/{branch}.json, last_verify.json")
    except OSError as exc:
        print(f"  [Verify] Could not write verification record: {exc}")


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
    4. Security scan (bandit + pattern matching)
    5. Code quality (ruff / flake8)
    6. Domain validation (import smoke tests, anti-pattern detection)
    7. LLM quality evaluation (DeepEval GEval + Faithfulness)
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 6: Verify] Project: {project_name}")

    # Resolve workspace path: worktree > target_dir > CWD
    workspace_info = state.get("workspace_info") or {}
    workspace = (
        workspace_info.get("worktree_path")
        or state.get("target_dir")
    )

    if workspace:
        print(f"  [Verify] Workspace: {workspace}")
    else:
        print("  [Verify] Workspace: using CWD (no worktree or target_dir)")

    layer_runners = [
        ("Layer 1: Running pytest...", _run_pytest),
        ("Layer 2: Checking test files...", _check_test_files_exist),
        ("Layer 3: Syntax check...", _check_syntax),
        ("Layer 4: Security scan...", _run_security_scan),
        ("Layer 5: Code quality...", _run_code_quality),
        ("Layer 6: Domain validation...", _run_domain_validation),
    ]

    layers: list[LayerResult] = []
    for label, runner in layer_runners:
        print(f"  [Verify] {label}")
        result = runner(workspace=workspace)
        layers.append(result)
        print(f"    Result: {result.status} — {result.details[:80]}")

    # Layer 7: DeepEval quality evaluation (needs state artifacts)
    print("  [Verify] Layer 7: LLM quality evaluation...")
    build_results_data = state.get("build_results", [])
    design_spec_data = state.get("design_spec") or {}
    deepeval_result = _run_deepeval(
        workspace=workspace,
        build_results=build_results_data,
        design_spec=design_spec_data,
    )
    layers.append(deepeval_result)
    print(f"    Result: {deepeval_result.status} — {deepeval_result.details[:80]}")

    # Determine overall status: pass/skip = green, fail = red
    all_green = all(lr.status in ("pass", "skip") for lr in layers)

    # Summary counts for logging
    passed = sum(1 for lr in layers if lr.status == "pass")
    failed = sum(1 for lr in layers if lr.status == "fail")
    skipped = sum(1 for lr in layers if lr.status == "skip")

    hash_input = json.dumps(
        [lr.model_dump() if hasattr(lr, "model_dump") else lr for lr in layers],
        sort_keys=True,
        default=str,
    )
    integrity_hash = hashlib.sha256(hash_input.encode()).hexdigest()

    record = VerificationRecord(
        all_green=all_green,
        layers=[lr for lr in layers],
        integrity_hash=integrity_hash,
        timestamp=datetime.now(timezone.utc),
    )

    verdict = "ALL GREEN" if all_green else "ISSUES FOUND"
    print(f"  [Verify] Overall: {verdict} ({passed} passed, {failed} failed, {skipped} skipped)")

    # Write verification record to disk (per-branch + last)
    _persist_record(record, workspace)

    return {
        "current_stage": "verify",
        "verify_record": record.model_dump(),
    }
