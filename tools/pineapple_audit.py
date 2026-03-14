"""
pineapple audit -- Check pipeline compliance and integrity.

Usage: python production-pipeline/tools/pineapple_audit.py [--project PATH] [--json]

Checks:
  1. Hookify rules: count rules, verify >= 16 (11 original + 5 pineapple)
  2. Verification records: check integrity_hash on all .pineapple/verify/*.json
  3. Pipeline tools: all required tools present in tools/
  4. Config: load and validate PineappleConfig
"""
from __future__ import annotations
import glob
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger("pineapple.audit")

@dataclass
class AuditCheck:
    name: str
    status: Literal["pass", "fail", "warn"]
    message: str
    details: list[str] = field(default_factory=list)

@dataclass
class AuditReport:
    checks: list[AuditCheck] = field(default_factory=list)

    @property
    def compliance_score(self) -> float:
        if not self.checks:
            return 0.0
        passed = sum(1 for c in self.checks if c.status == "pass")
        return round(passed / len(self.checks) * 100, 1)

    @property
    def overall_pass(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "compliance_score": self.compliance_score,
            "overall": "pass" if self.overall_pass else "fail",
            "checks": [
                {"name": c.name, "status": c.status, "message": c.message, "details": c.details}
                for c in self.checks
            ],
        }

def audit_hookify_rules() -> AuditCheck:
    """Check hookify rules are present and sufficient."""
    home = Path.home()
    rules = list(home.glob(".claude/hookify.*.local.md"))
    pineapple_rules = [r for r in rules if "pineapple" in r.name]

    if len(rules) >= 16 and len(pineapple_rules) >= 5:
        return AuditCheck("Hookify rules", "pass", f"{len(rules)} rules ({len(pineapple_rules)} pineapple)")
    elif len(rules) >= 11:
        return AuditCheck("Hookify rules", "warn", f"{len(rules)} rules but only {len(pineapple_rules)} pineapple gates",
                         details=[f"Expected 5 pineapple gates, found {len(pineapple_rules)}"])
    else:
        return AuditCheck("Hookify rules", "fail", f"Only {len(rules)} rules (expected >= 16)")

def audit_verification_records(project_path: Path) -> AuditCheck:
    """Check all verification records have valid integrity hashes."""
    verify_dir = project_path / ".pineapple" / "verify"
    if not verify_dir.is_dir():
        return AuditCheck("Verification records", "warn", "No .pineapple/verify/ directory")

    records = list(verify_dir.glob("*.json"))
    if not records:
        return AuditCheck("Verification records", "warn", "No verification records found")

    valid = 0
    invalid = []
    for record_path in records:
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
            # Re-compute integrity hash
            payload = f"{data['evidence_hash']}|{data['run_id']}|{data['branch']}|{data['timestamp']}"
            expected = hashlib.sha256(payload.encode()).hexdigest()
            if expected == data.get("integrity_hash"):
                valid += 1
            else:
                invalid.append(record_path.name)
        except Exception as e:
            invalid.append(f"{record_path.name} (error: {e})")

    if invalid:
        return AuditCheck("Verification records", "fail", f"{len(invalid)}/{len(records)} records have invalid integrity",
                         details=invalid)
    return AuditCheck("Verification records", "pass", f"{valid}/{len(records)} records valid")

def audit_pipeline_tools() -> AuditCheck:
    """Check all required pipeline tools are present."""
    tools_dir = Path(__file__).parent
    required = [
        "apply_pipeline.py", "pipeline_state.py", "pineapple_config.py",
        "pineapple_doctor.py", "pineapple_verify.py", "pineapple_evolve.py",
        "pipeline_tracer.py",
    ]
    missing = [t for t in required if not (tools_dir / t).is_file()]

    if missing:
        return AuditCheck("Pipeline tools", "fail", f"Missing: {', '.join(missing)}")
    return AuditCheck("Pipeline tools", "pass", f"All {len(required)} tools present")

def audit_config() -> AuditCheck:
    """Check pipeline config is valid."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from pineapple_config import load_config, validate_config
        config = load_config()
        warnings = validate_config(config)
        if warnings:
            return AuditCheck("Config", "warn", f"Valid with {len(warnings)} warnings", details=warnings)
        return AuditCheck("Config", "pass", "Config valid")
    except Exception as e:
        return AuditCheck("Config", "fail", f"Config error: {e}")

def run_audit(project_path: Path | None = None) -> AuditReport:
    """Run all audit checks."""
    report = AuditReport()
    report.checks.append(audit_hookify_rules())
    if project_path:
        report.checks.append(audit_verification_records(project_path))
    report.checks.append(audit_pipeline_tools())
    report.checks.append(audit_config())
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pineapple Pipeline Audit")
    parser.add_argument("--project", type=Path, help="Project path to audit")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    report = run_audit(args.project)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"\nPineapple Audit v1.0.0")
        print(f"========================")
        for c in report.checks:
            tag = {"pass": " PASS ", "fail": " FAIL ", "warn": " WARN "}[c.status]
            print(f"  [{tag}] {c.name}: {c.message}")
            for d in c.details:
                print(f"         - {d}")
        print(f"\nCompliance: {report.compliance_score}%")
        print(f"Overall: {'PASS' if report.overall_pass else 'FAIL'}")

    sys.exit(0 if report.overall_pass else 1)
