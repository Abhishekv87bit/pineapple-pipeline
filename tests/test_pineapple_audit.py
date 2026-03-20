"""Tests for pineapple_audit.py -- pipeline compliance and integrity checker.

All tests use tmp_path fixtures and have no real filesystem dependencies.
"""

import hashlib
import json

import pytest

from pineapple_audit import (
    AuditCheck,
    AuditReport,
    audit_hookify_rules,
    audit_pipeline_tools,
    audit_verification_records,
    audit_config,
    run_audit,
)


# ---------------------------------------------------------------------------
# AuditReport dataclass
# ---------------------------------------------------------------------------


class TestAuditReport:
    def test_empty_report_score_zero(self):
        report = AuditReport()
        assert report.compliance_score == 0.0

    def test_all_pass_score_100(self):
        report = AuditReport(
            checks=[
                AuditCheck("a", "pass", "ok"),
                AuditCheck("b", "pass", "ok"),
            ]
        )
        assert report.compliance_score == 100.0

    def test_half_pass_score_50(self):
        report = AuditReport(
            checks=[
                AuditCheck("a", "pass", "ok"),
                AuditCheck("b", "fail", "bad"),
            ]
        )
        assert report.compliance_score == 50.0

    def test_warn_does_not_count_as_pass(self):
        report = AuditReport(
            checks=[
                AuditCheck("a", "warn", "meh"),
            ]
        )
        assert report.compliance_score == 0.0

    def test_overall_pass_all_pass(self):
        report = AuditReport(
            checks=[
                AuditCheck("a", "pass", "ok"),
                AuditCheck("b", "pass", "ok"),
            ]
        )
        assert report.overall_pass is True

    def test_overall_pass_with_warn(self):
        """Warn does not block overall_pass -- only fail does."""
        report = AuditReport(
            checks=[
                AuditCheck("a", "pass", "ok"),
                AuditCheck("b", "warn", "meh"),
            ]
        )
        assert report.overall_pass is True

    def test_overall_fail_with_fail(self):
        report = AuditReport(
            checks=[
                AuditCheck("a", "pass", "ok"),
                AuditCheck("b", "fail", "bad"),
            ]
        )
        assert report.overall_pass is False

    def test_to_dict_structure(self):
        report = AuditReport(
            checks=[AuditCheck("check1", "pass", "all good", details=["detail1"])]
        )
        d = report.to_dict()
        assert d["compliance_score"] == 100.0
        assert d["overall"] == "pass"
        assert len(d["checks"]) == 1
        assert d["checks"][0]["name"] == "check1"
        assert d["checks"][0]["status"] == "pass"
        assert d["checks"][0]["details"] == ["detail1"]


# ---------------------------------------------------------------------------
# audit_hookify_rules
# ---------------------------------------------------------------------------


class TestAuditHookifyRules:
    def test_pass_with_sufficient_rules(self, tmp_path, monkeypatch):
        """16+ rules with 5+ pineapple rules should pass."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Create 11 base rules + 5 pineapple rules
        for i in range(11):
            (claude_dir / f"hookify.rule{i}.local.md").write_text(f"rule {i}")
        for i in range(5):
            (claude_dir / f"hookify.pineapple{i}.local.md").write_text(f"pineapple rule {i}")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = audit_hookify_rules()
        assert result.status == "pass"
        assert "16" in result.message

    def test_warn_with_11_rules_no_pineapple(self, tmp_path, monkeypatch):
        """11+ base rules but 0 pineapple rules should warn."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        for i in range(11):
            (claude_dir / f"hookify.rule{i}.local.md").write_text(f"rule {i}")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = audit_hookify_rules()
        assert result.status == "warn"
        assert "0 pineapple" in result.message

    def test_fail_with_zero_rules(self, tmp_path, monkeypatch):
        """No rules at all should fail."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = audit_hookify_rules()
        assert result.status == "fail"
        assert "0" in result.message

    def test_fail_with_few_rules(self, tmp_path, monkeypatch):
        """Under 11 rules should fail."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        for i in range(3):
            (claude_dir / f"hookify.rule{i}.local.md").write_text(f"rule {i}")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = audit_hookify_rules()
        assert result.status == "fail"
        assert "3" in result.message


# ---------------------------------------------------------------------------
# audit_verification_records
# ---------------------------------------------------------------------------


def _make_valid_record(path, run_id="run-001", branch="main"):
    """Helper: create a verification record with valid integrity hash."""
    evidence_hash = hashlib.sha256(b"test evidence").hexdigest()
    timestamp = "2025-06-01T12:00:00"
    payload = f"{evidence_hash}|{run_id}|{branch}|{timestamp}"
    integrity_hash = hashlib.sha256(payload.encode()).hexdigest()
    data = {
        "evidence_hash": evidence_hash,
        "run_id": run_id,
        "branch": branch,
        "timestamp": timestamp,
        "integrity_hash": integrity_hash,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


class TestAuditVerificationRecords:
    def test_no_verify_dir_warns(self, tmp_path):
        result = audit_verification_records(tmp_path)
        assert result.status == "warn"
        assert "No .pineapple/verify/" in result.message

    def test_empty_verify_dir_warns(self, tmp_path):
        (tmp_path / ".pineapple" / "verify").mkdir(parents=True)
        result = audit_verification_records(tmp_path)
        assert result.status == "warn"
        assert "No verification records" in result.message

    def test_valid_records_pass(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_valid_record(verify_dir / "rec1.json", run_id="r1")
        _make_valid_record(verify_dir / "rec2.json", run_id="r2")

        result = audit_verification_records(tmp_path)
        assert result.status == "pass"
        assert "2/2" in result.message

    def test_tampered_record_fails(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        _make_valid_record(verify_dir / "good.json", run_id="r1")
        # Create a tampered record
        _make_valid_record(verify_dir / "bad.json", run_id="r2")
        bad_data = json.loads((verify_dir / "bad.json").read_text())
        bad_data["integrity_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
        (verify_dir / "bad.json").write_text(json.dumps(bad_data))

        result = audit_verification_records(tmp_path)
        assert result.status == "fail"
        assert "1/2" in result.message
        assert "bad.json" in result.details[0]

    def test_corrupt_json_fails(self, tmp_path):
        verify_dir = tmp_path / ".pineapple" / "verify"
        verify_dir.mkdir(parents=True)
        (verify_dir / "corrupt.json").write_text("{not valid json", encoding="utf-8")

        result = audit_verification_records(tmp_path)
        assert result.status == "fail"
        assert "corrupt.json" in result.details[0]


# ---------------------------------------------------------------------------
# audit_pipeline_tools
# ---------------------------------------------------------------------------


class TestAuditPipelineTools:
    def test_pass_when_all_tools_present(self):
        """All required tools exist in the real tools/ directory."""
        result = audit_pipeline_tools()
        assert result.status == "pass"
        assert "7" in result.message

    def test_reports_correct_tool_count(self):
        result = audit_pipeline_tools()
        assert "7 tools present" in result.message


# ---------------------------------------------------------------------------
# audit_config
# ---------------------------------------------------------------------------


class TestAuditConfig:
    def test_default_config_passes(self, monkeypatch, tmp_path):
        """Default config with no files should pass (Pydantic defaults are valid)."""
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: tmp_path / "nonexistent" / "config.yaml",
        )
        result = audit_config()
        assert result.status == "pass"
        assert "valid" in result.message.lower()


# ---------------------------------------------------------------------------
# run_audit (integration)
# ---------------------------------------------------------------------------


class TestRunAudit:
    def test_run_audit_without_project_path(self, tmp_path, monkeypatch):
        """Without project_path, should skip verification records check."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        for i in range(16):
            name = f"hookify.pineapple{i}.local.md" if i < 5 else f"hookify.rule{i}.local.md"
            (claude_dir / name).write_text(f"rule {i}")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: tmp_path / "nonexistent" / "config.yaml",
        )

        report = run_audit(project_path=None)
        check_names = [c.name for c in report.checks]
        assert "Verification records" not in check_names
        # Should have hookify + pipeline tools + config
        assert len(report.checks) == 3

    def test_run_audit_with_project_path(self, tmp_path, monkeypatch):
        """With project_path, should include verification records check."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        for i in range(16):
            name = f"hookify.pineapple{i}.local.md" if i < 5 else f"hookify.rule{i}.local.md"
            (claude_dir / name).write_text(f"rule {i}")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: tmp_path / "nonexistent" / "config.yaml",
        )

        report = run_audit(project_path=tmp_path)
        check_names = [c.name for c in report.checks]
        assert "Verification records" in check_names
        assert len(report.checks) == 4

    def test_run_audit_compliance_score(self, tmp_path, monkeypatch):
        """Compliance score should reflect actual pass/fail ratio."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Zero rules -> fail on hookify
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: tmp_path / "nonexistent" / "config.yaml",
        )

        report = run_audit(project_path=None)
        # hookify=fail, pipeline_tools=pass, config=pass => 2/3
        assert report.compliance_score == pytest.approx(66.7, abs=0.1)
        assert report.overall_pass is False
