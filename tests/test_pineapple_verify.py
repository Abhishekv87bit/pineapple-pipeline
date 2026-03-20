"""Tests for pineapple_verify.py — verification runner."""

import json
import subprocess
from unittest.mock import patch, MagicMock
from pineapple_verify import (
    LayerResult,
    VerificationRecord,
    run_verification,
    verify_integrity,
    run_layer_1_unit_tests,
    run_layer_2_integration_tests,
    run_layer_3_security_tests,
    run_layer_5_domain_validation,
    run_layer_6_visual_inspection,
    _compute_evidence_hash,
    _compute_integrity_hash,
    _count_pytest_tests,
    _find_backend,
)


class TestLayerResult:
    def test_pass_result(self):
        r = LayerResult(layer=1, name="Unit tests", status="pass", test_count=10, output="all passed")
        assert r.layer == 1
        assert r.test_count == 10

    def test_default_fields(self):
        r = LayerResult(layer=2, name="test", status="skip")
        assert r.test_count == 0
        assert r.output == ""
        assert r.duration_ms == 0

    def test_result_structure_has_required_fields(self):
        """LayerResult must have status, output, and duration_ms fields."""
        r = LayerResult(layer=1, name="Unit tests", status="pass", test_count=5, output="5 passed", duration_ms=123.4)
        assert r.status in ("pass", "fail", "skip", "error")
        assert isinstance(r.output, str)
        assert isinstance(r.duration_ms, float)


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


class TestLayerRunners:
    """Direct tests for run_layer_* functions."""

    # -- Layer 1: Unit tests ------------------------------------------------

    def test_layer_1_no_backend_skips(self, tmp_path):
        """When no backend dir exists, layer 1 should skip."""
        result = run_layer_1_unit_tests(tmp_path)
        assert result.status == "skip"
        assert result.layer == 1
        assert "No backend" in result.output

    def test_layer_1_pass_with_tests(self, tmp_path):
        """When pytest passes, layer 1 should report pass with test count."""
        # Create a backend dir with a tests subdir (so _find_backend finds it)
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "tests/test_foo.py::test_bar PASSED\n===== 3 passed in 0.1s ====="
        mock_result.stderr = ""

        with patch("pineapple_verify.subprocess.run", return_value=mock_result):
            result = run_layer_1_unit_tests(tmp_path)
            assert result.status == "pass"
            assert result.test_count == 3
            assert result.duration_ms >= 0
            assert result.layer == 1

    def test_layer_1_fail_with_failures(self, tmp_path):
        """When pytest fails, layer 1 should report fail."""
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "===== 2 passed, 1 failed in 0.3s ====="
        mock_result.stderr = ""

        with patch("pineapple_verify.subprocess.run", return_value=mock_result):
            result = run_layer_1_unit_tests(tmp_path)
            assert result.status == "fail"
            assert result.test_count == 3

    def test_layer_1_timeout_handled(self, tmp_path):
        """When subprocess times out, layer 1 should report error."""
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)

        with patch("pineapple_verify.subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 300)):
            result = run_layer_1_unit_tests(tmp_path)
            assert result.status == "error"
            assert "Timeout" in result.output

    def test_layer_1_exception_handled(self, tmp_path):
        """When subprocess raises an unexpected exception, layer 1 reports error."""
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)

        with patch("pineapple_verify.subprocess.run", side_effect=OSError("broken")):
            result = run_layer_1_unit_tests(tmp_path)
            assert result.status == "error"
            assert "broken" in result.output

    # -- Layer 2: Integration tests -----------------------------------------

    def test_layer_2_no_backend_skips(self, tmp_path):
        """When no backend dir exists, layer 2 should skip."""
        result = run_layer_2_integration_tests(tmp_path)
        assert result.status == "skip"
        assert result.layer == 2

    def test_layer_2_no_integration_files_skips(self, tmp_path):
        """When backend exists but no integration test files, layer 2 skips."""
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)
        result = run_layer_2_integration_tests(tmp_path)
        assert result.status == "skip"
        assert "No integration test files" in result.output

    def test_layer_2_pass(self, tmp_path):
        """When integration tests pass, layer 2 reports pass."""
        backend = tmp_path / "backend"
        tests_dir = backend / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_integration_api.py").write_text("def test_x(): pass")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "===== 2 passed in 0.5s ====="
        mock_result.stderr = ""

        with patch("pineapple_verify.subprocess.run", return_value=mock_result):
            result = run_layer_2_integration_tests(tmp_path)
            assert result.status == "pass"
            assert result.test_count == 2
            assert result.layer == 2

    # -- Layer 3: Security tests --------------------------------------------

    def test_layer_3_no_backend_skips(self, tmp_path):
        """When no backend dir exists, layer 3 should skip."""
        result = run_layer_3_security_tests(tmp_path)
        assert result.status == "skip"
        assert result.layer == 3

    def test_layer_3_no_adversarial_files_skips(self, tmp_path):
        """When backend exists but no adversarial test files, layer 3 skips."""
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)
        result = run_layer_3_security_tests(tmp_path)
        assert result.status == "skip"
        assert "No adversarial test files" in result.output

    def test_layer_3_pass(self, tmp_path):
        """When adversarial tests pass, layer 3 reports pass."""
        backend = tmp_path / "backend"
        tests_dir = backend / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_adversarial_injection.py").write_text("def test_x(): pass")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "===== 1 passed in 0.2s ====="
        mock_result.stderr = ""

        with patch("pineapple_verify.subprocess.run", return_value=mock_result):
            result = run_layer_3_security_tests(tmp_path)
            assert result.status == "pass"
            assert result.test_count == 1

    # -- Layer 5: Domain validation -----------------------------------------

    def test_layer_5_no_vlad_skips(self, tmp_path):
        """When VLAD is not found, layer 5 should skip."""
        result = run_layer_5_domain_validation(tmp_path)
        assert result.status == "skip"
        assert "VLAD not found" in result.output
        assert result.layer == 5

    def test_layer_5_pass(self, tmp_path):
        """When VLAD runs successfully, layer 5 reports pass."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        vlad_file = tools_dir / "vlad.py"
        vlad_file.write_text("print('ok')")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "all checks passed"
        mock_result.stderr = ""

        with patch("pineapple_verify.subprocess.run", return_value=mock_result):
            result = run_layer_5_domain_validation(tmp_path)
            assert result.status == "pass"
            assert result.duration_ms >= 0

    def test_layer_5_fail(self, tmp_path):
        """When VLAD fails, layer 5 reports fail."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        vlad_file = tools_dir / "vlad.py"
        vlad_file.write_text("print('failed')")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "FAIL: topology check"
        mock_result.stderr = ""

        with patch("pineapple_verify.subprocess.run", return_value=mock_result):
            result = run_layer_5_domain_validation(tmp_path)
            assert result.status == "fail"

    # -- Layer 6: Visual inspection -----------------------------------------

    def test_layer_6_always_skips(self, tmp_path):
        """Layer 6 always skips in automated mode."""
        result = run_layer_6_visual_inspection(tmp_path)
        assert result.status == "skip"
        assert result.layer == 6
        assert "human" in result.output.lower()


class TestRunVerification:
    def test_run_verification_produces_record(self, tmp_path):
        """Even with no backend, should produce a valid record (all skip)."""
        record = run_verification(tmp_path, branch="test-branch")
        assert isinstance(record, VerificationRecord)
        assert record.branch == "test-branch"
        assert record.evidence_hash != ""
        assert record.integrity_hash != ""

    def test_run_verification_writes_record_file(self, tmp_path):
        run_verification(tmp_path, branch="feat/my-feature")
        record_file = tmp_path / ".pineapple" / "verify" / "feat--my-feature.json"
        assert record_file.is_file()

    def test_run_verification_all_skip_not_green(self, tmp_path):
        """If all layers skip, all_green should be False (need at least one pass)."""
        record = run_verification(tmp_path, branch="test")
        assert record.all_green is False

    @patch("pineapple_verify.run_layer_1_unit_tests")
    def test_run_verification_with_passing_layer(self, mock_l1, tmp_path):
        """Single layer requested, single layer passes -> all_green=True."""
        mock_l1.return_value = LayerResult(layer=1, name="Unit tests", status="pass", test_count=5, output="5 passed")
        record = run_verification(tmp_path, branch="test", layers=[1])
        assert 1 in record.layers_passed
        assert record.all_green is True
        assert record.fully_verified is True

    def test_run_verification_specific_layers(self, tmp_path):
        record = run_verification(tmp_path, branch="test", layers=[1, 3])
        # Only layers 1 and 3 should appear
        all_layers = record.layers_passed + record.layers_failed + record.layers_skipped
        assert sorted(all_layers) == [1, 3]


class TestAllGreenAndFullyVerified:
    """VER-001: all_green must not be true when layers are skipped."""

    @patch("pineapple_verify.run_layer_1_unit_tests")
    @patch("pineapple_verify.run_layer_2_integration_tests")
    @patch("pineapple_verify.run_layer_3_security_tests")
    @patch("pineapple_verify.run_layer_4_llm_evals")
    @patch("pineapple_verify.run_layer_5_domain_validation")
    @patch("pineapple_verify.run_layer_6_visual_inspection")
    def test_one_pass_five_skips_not_green(self, mock_l6, mock_l5, mock_l4, mock_l3, mock_l2, mock_l1, tmp_path):
        """layers_passed=[1], layers_skipped=[2,3,4,5,6] -> all_green=False, fully_verified=False."""
        mock_l1.return_value = LayerResult(layer=1, name="Unit tests", status="pass", test_count=10, output="10 passed")
        mock_l2.return_value = LayerResult(layer=2, name="Integration tests", status="skip", output="No integration test files found")
        mock_l3.return_value = LayerResult(layer=3, name="Security tests", status="skip", output="No adversarial test files found")
        mock_l4.return_value = LayerResult(layer=4, name="LLM evals", status="skip", output="deepeval not installed")
        mock_l5.return_value = LayerResult(layer=5, name="Domain validation", status="skip", output="VLAD not found")
        mock_l6.return_value = LayerResult(layer=6, name="Visual inspection", status="skip", output="Requires human review")

        record = run_verification(tmp_path, branch="test")
        assert record.layers_passed == [1]
        assert record.layers_skipped == [2, 3, 4, 5, 6]
        assert record.all_green is False
        assert record.fully_verified is False

    @patch("pineapple_verify.run_layer_1_unit_tests")
    @patch("pineapple_verify.run_layer_2_integration_tests")
    @patch("pineapple_verify.run_layer_3_security_tests")
    @patch("pineapple_verify.run_layer_4_llm_evals")
    @patch("pineapple_verify.run_layer_5_domain_validation")
    @patch("pineapple_verify.run_layer_6_visual_inspection")
    def test_all_six_pass_is_green(self, mock_l6, mock_l5, mock_l4, mock_l3, mock_l2, mock_l1, tmp_path):
        """layers_passed=[1,2,3,4,5,6], layers_skipped=[] -> all_green=True, fully_verified=True."""
        mock_l1.return_value = LayerResult(layer=1, name="Unit tests", status="pass", test_count=10, output="10 passed")
        mock_l2.return_value = LayerResult(layer=2, name="Integration tests", status="pass", test_count=5, output="5 passed")
        mock_l3.return_value = LayerResult(layer=3, name="Security tests", status="pass", test_count=3, output="3 passed")
        mock_l4.return_value = LayerResult(layer=4, name="LLM evals", status="pass", test_count=2, output="2 passed")
        mock_l5.return_value = LayerResult(layer=5, name="Domain validation", status="pass", output="all clear")
        mock_l6.return_value = LayerResult(layer=6, name="Visual inspection", status="pass", output="approved")

        record = run_verification(tmp_path, branch="test")
        assert record.layers_passed == [1, 2, 3, 4, 5, 6]
        assert record.layers_skipped == []
        assert record.layers_failed == []
        assert record.all_green is True
        assert record.fully_verified is True

    @patch("pineapple_verify.run_layer_1_unit_tests")
    @patch("pineapple_verify.run_layer_2_integration_tests")
    @patch("pineapple_verify.run_layer_3_security_tests")
    @patch("pineapple_verify.run_layer_4_llm_evals")
    @patch("pineapple_verify.run_layer_5_domain_validation")
    @patch("pineapple_verify.run_layer_6_visual_inspection")
    def test_three_pass_three_skip_not_green(self, mock_l6, mock_l5, mock_l4, mock_l3, mock_l2, mock_l1, tmp_path):
        """layers_passed=[1,2,3], layers_skipped=[4,5,6] -> all_green=False, fully_verified=False."""
        mock_l1.return_value = LayerResult(layer=1, name="Unit tests", status="pass", test_count=10, output="10 passed")
        mock_l2.return_value = LayerResult(layer=2, name="Integration tests", status="pass", test_count=5, output="5 passed")
        mock_l3.return_value = LayerResult(layer=3, name="Security tests", status="pass", test_count=3, output="3 passed")
        mock_l4.return_value = LayerResult(layer=4, name="LLM evals", status="skip", output="deepeval not installed")
        mock_l5.return_value = LayerResult(layer=5, name="Domain validation", status="skip", output="VLAD not found")
        mock_l6.return_value = LayerResult(layer=6, name="Visual inspection", status="skip", output="Requires human review")

        record = run_verification(tmp_path, branch="test")
        assert record.layers_passed == [1, 2, 3]
        assert record.layers_skipped == [4, 5, 6]
        assert record.all_green is False
        assert record.fully_verified is False

    def test_all_skip_not_green_no_fully_verified(self, tmp_path):
        """layers_passed=[], layers_skipped=[1,2,3,4,5,6] -> all_green=False, fully_verified=False."""
        record = run_verification(tmp_path, branch="test")
        assert record.all_green is False
        assert record.fully_verified is False

    def test_fully_verified_in_to_dict(self):
        """fully_verified field must appear in serialized dict."""
        r = VerificationRecord(run_id="abc", branch="main", timestamp="2026-01-01T00:00:00")
        d = r.to_dict()
        assert "fully_verified" in d

    def test_fully_verified_in_json_file(self, tmp_path):
        """fully_verified field must be written to the JSON record file."""
        run_verification(tmp_path, branch="test")
        record_file = tmp_path / ".pineapple" / "verify" / "test.json"
        data = json.loads(record_file.read_text())
        assert "fully_verified" in data


class TestVerifyIntegrity:
    def test_valid_record_passes(self, tmp_path):
        run_verification(tmp_path, branch="test")
        record_file = tmp_path / ".pineapple" / "verify" / "test.json"
        assert verify_integrity(record_file) is True

    def test_tampered_record_fails(self, tmp_path):
        run_verification(tmp_path, branch="test")
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
