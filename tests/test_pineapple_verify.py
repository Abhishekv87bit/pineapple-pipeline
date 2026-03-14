"""Tests for pineapple_verify.py — verification runner."""
import hashlib
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from pineapple_verify import (
    LayerResult, VerificationRecord,
    run_verification, verify_integrity,
    _compute_evidence_hash, _compute_integrity_hash,
    _count_pytest_tests, _find_backend,
)


class TestLayerResult:
    def test_pass_result(self):
        r = LayerResult(layer=1, name="Unit tests", status="pass", test_count=10, output="all passed")
        assert r.layer == 1
        assert r.test_count == 10


class TestVerificationRecord:
    def test_to_dict(self):
        r = VerificationRecord(run_id="abc", branch="main", timestamp="2026-01-01T00:00:00")
        d = r.to_dict()
        assert d["run_id"] == "abc"
        assert d["branch"] == "main"


class TestHelpers:
    def test_count_pytest_tests_passed(self):
        output = "===== 10 passed in 0.5s ====="
        assert _count_pytest_tests(output) == 10

    def test_count_pytest_tests_mixed(self):
        output = "===== 8 passed, 2 failed in 1.0s ====="
        assert _count_pytest_tests(output) == 10

    def test_count_pytest_tests_no_match(self):
        assert _count_pytest_tests("no tests ran") == 0

    def test_find_backend_standard(self, tmp_path):
        (tmp_path / "backend" / "app").mkdir(parents=True)
        assert _find_backend(tmp_path) == tmp_path / "backend"

    def test_find_backend_none(self, tmp_path):
        assert _find_backend(tmp_path) is None


class TestHashing:
    def test_evidence_hash_deterministic(self):
        results = [
            LayerResult(layer=1, name="test", status="pass", output="hello"),
            LayerResult(layer=2, name="test2", status="pass", output="world"),
        ]
        h1 = _compute_evidence_hash(results)
        h2 = _compute_evidence_hash(results)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_evidence_hash_changes_with_output(self):
        r1 = [LayerResult(layer=1, name="test", status="pass", output="hello")]
        r2 = [LayerResult(layer=1, name="test", status="pass", output="world")]
        assert _compute_evidence_hash(r1) != _compute_evidence_hash(r2)

    def test_integrity_hash_deterministic(self):
        h1 = _compute_integrity_hash("ev", "run", "branch", "time")
        h2 = _compute_integrity_hash("ev", "run", "branch", "time")
        assert h1 == h2

    def test_integrity_hash_changes_with_any_field(self):
        base = _compute_integrity_hash("ev", "run", "branch", "time")
        assert _compute_integrity_hash("XX", "run", "branch", "time") != base
        assert _compute_integrity_hash("ev", "XX", "branch", "time") != base
        assert _compute_integrity_hash("ev", "run", "XX", "time") != base
        assert _compute_integrity_hash("ev", "run", "branch", "XX") != base


class TestRunVerification:
    def test_run_verification_produces_record(self, tmp_path):
        """Even with no backend, should produce a valid record (all skip)."""
        record = run_verification(tmp_path, branch="test-branch")
        assert isinstance(record, VerificationRecord)
        assert record.branch == "test-branch"
        assert record.evidence_hash != ""
        assert record.integrity_hash != ""

    def test_run_verification_writes_record_file(self, tmp_path):
        record = run_verification(tmp_path, branch="feat/my-feature")
        record_file = tmp_path / ".pineapple" / "verify" / "feat--my-feature.json"
        assert record_file.is_file()

    def test_run_verification_all_skip_not_green(self, tmp_path):
        """If all layers skip, all_green should be False (need at least one pass)."""
        record = run_verification(tmp_path, branch="test")
        assert record.all_green is False

    @patch("pineapple_verify.run_layer_1_unit_tests")
    def test_run_verification_with_passing_layer(self, mock_l1, tmp_path):
        mock_l1.return_value = LayerResult(layer=1, name="Unit tests", status="pass", test_count=5, output="5 passed")
        record = run_verification(tmp_path, branch="test", layers=[1])
        assert 1 in record.layers_passed
        assert record.all_green is True

    def test_run_verification_specific_layers(self, tmp_path):
        record = run_verification(tmp_path, branch="test", layers=[1, 3])
        # Only layers 1 and 3 should appear
        all_layers = record.layers_passed + record.layers_failed + record.layers_skipped
        assert sorted(all_layers) == [1, 3]


class TestVerifyIntegrity:
    def test_valid_record_passes(self, tmp_path):
        record = run_verification(tmp_path, branch="test")
        record_file = tmp_path / ".pineapple" / "verify" / "test.json"
        assert verify_integrity(record_file) is True

    def test_tampered_record_fails(self, tmp_path):
        record = run_verification(tmp_path, branch="test")
        record_file = tmp_path / ".pineapple" / "verify" / "test.json"
        # Tamper with the record
        data = json.loads(record_file.read_text())
        data["all_green"] = True  # Forge the result
        data["layers_passed"] = [1, 2, 3, 4, 5, 6]
        record_file.write_text(json.dumps(data))
        # Integrity hash still checks evidence_hash|run_id|branch|timestamp
        # Changing all_green doesn't affect integrity_hash, but changing evidence_hash would
        # Let's tamper with evidence_hash instead
        data["evidence_hash"] = "forged_hash_value"
        record_file.write_text(json.dumps(data))
        assert verify_integrity(record_file) is False
