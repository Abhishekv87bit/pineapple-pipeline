"""Tests for pineapple_doctor.py — health check tool."""

from unittest.mock import patch, MagicMock
from pineapple_doctor import (
    CheckResult,
    DoctorReport,
    run_doctor,
    check_docker,
    check_templates,
    check_pydantic,
    check_hookify_rules,
    check_config,
    check_pipeline_tools,
    check_python_package,
)


class TestCheckResult:
    def test_pass_result(self):
        r = CheckResult(name="test", status="pass", message="ok")
        assert r.status == "pass"

    def test_fail_result(self):
        r = CheckResult(name="test", status="fail", message="bad", required=True)
        assert r.required is True


class TestDoctorReport:
    def test_overall_pass_all_pass(self):
        report = DoctorReport(
            checks=[
                CheckResult("a", "pass", "ok"),
                CheckResult("b", "pass", "ok"),
            ]
        )
        assert report.overall_pass is True

    def test_overall_fail_required_fail(self):
        report = DoctorReport(
            checks=[
                CheckResult("a", "pass", "ok"),
                CheckResult("b", "fail", "bad", required=True),
            ]
        )
        assert report.overall_pass is False

    def test_overall_pass_optional_fail(self):
        report = DoctorReport(
            checks=[
                CheckResult("a", "pass", "ok"),
                CheckResult("b", "fail", "bad", required=False),
            ]
        )
        assert report.overall_pass is True

    def test_skip_does_not_affect_overall(self):
        report = DoctorReport(
            checks=[
                CheckResult("a", "pass", "ok"),
                CheckResult("b", "skip", "optional", required=False),
            ]
        )
        assert report.overall_pass is True

    def test_to_dict(self):
        report = DoctorReport(checks=[CheckResult("a", "pass", "ok")])
        d = report.to_dict()
        assert d["overall"] == "pass"
        assert len(d["checks"]) == 1


class TestIndividualChecks:
    @patch("subprocess.run")
    def test_check_docker_pass(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Docker info")
        result = check_docker()
        assert result.status == "pass"

    @patch("subprocess.run", side_effect=FileNotFoundError("docker not found"))
    def test_check_docker_not_found(self, mock_run):
        result = check_docker()
        assert result.status == "fail"

    def test_check_pydantic_pass(self):
        result = check_pydantic()
        assert result.status == "pass"
        assert "installed" in result.message

    def test_check_pydantic_not_installed(self):
        """When pydantic is not importable, check_pydantic should fail."""
        with patch("pineapple_doctor.importlib.import_module", side_effect=ImportError("no pydantic")):
            from pineapple_doctor import check_pydantic as cp
            result = cp()
            assert result.status == "fail"
            assert result.required is True

    def test_check_python_package_installed(self):
        """check_python_package should pass for a package that exists."""
        result = check_python_package("json", required=False)
        assert result.status == "pass"

    def test_check_python_package_missing_optional(self):
        """Missing optional package should skip."""
        result = check_python_package("nonexistent_fake_package_xyz", required=False)
        assert result.status == "skip"
        assert result.required is False

    def test_check_python_package_missing_required(self):
        """Missing required package should fail."""
        result = check_python_package("nonexistent_fake_package_xyz", required=True)
        assert result.status == "fail"
        assert result.required is True


class TestCheckTemplates:
    def test_check_templates_pass_enough_files(self, tmp_path):
        """When templates dir has >= 11 files, check should pass."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        for i in range(12):
            (templates_dir / f"template_{i}.py").write_text("content")

        # Simpler: patch the templates_dir variable inside the function
        # check_templates computes: Path(__file__).resolve().parent.parent / "templates"
        # We can patch Path to control this chain.
        from pathlib import Path as RealPath

        fake_tools_dir = tmp_path / "tools"
        fake_tools_dir.mkdir()
        fake_file = fake_tools_dir / "pineapple_doctor.py"
        fake_file.write_text("")

        with patch("pineapple_doctor.__file__", str(fake_file)):
            result = check_templates()
            assert result.status == "pass"
            assert result.name == "templates"
            assert "12" in result.message

    def test_check_templates_fail_too_few_files(self, tmp_path):
        """When templates dir has < 11 files, check should fail."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        for i in range(3):
            (templates_dir / f"template_{i}.py").write_text("content")

        fake_tools_dir = tmp_path / "tools"
        fake_tools_dir.mkdir()
        fake_file = fake_tools_dir / "pineapple_doctor.py"
        fake_file.write_text("")

        with patch("pineapple_doctor.__file__", str(fake_file)):
            result = check_templates()
            assert result.status == "fail"
            assert "3" in result.message
            assert result.required is True

    def test_check_templates_fail_no_directory(self, tmp_path):
        """When templates dir does not exist, check should fail."""
        fake_tools_dir = tmp_path / "tools"
        fake_tools_dir.mkdir()
        fake_file = fake_tools_dir / "pineapple_doctor.py"
        fake_file.write_text("")

        with patch("pineapple_doctor.__file__", str(fake_file)):
            result = check_templates()
            assert result.status == "fail"
            assert "not found" in result.message


class TestCheckHookifyRules:
    def test_hookify_rules_pass_enough_files(self, tmp_path):
        """When >= 11 hookify rule files exist, check should pass."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        for i in range(11):
            (claude_dir / f"hookify.rule{i}.local.md").write_text("rule content")

        with patch("pineapple_doctor.Path.home", return_value=tmp_path):
            result = check_hookify_rules()
            assert result.status == "pass"
            assert "11" in result.message

    def test_hookify_rules_fail_too_few(self, tmp_path):
        """When < 11 hookify rule files exist, check should fail."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        for i in range(3):
            (claude_dir / f"hookify.rule{i}.local.md").write_text("rule content")

        with patch("pineapple_doctor.Path.home", return_value=tmp_path):
            result = check_hookify_rules()
            assert result.status == "fail"
            assert "3" in result.message

    def test_hookify_rules_fail_no_files(self, tmp_path):
        """When no hookify rule files exist, check should fail."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        with patch("pineapple_doctor.Path.home", return_value=tmp_path):
            result = check_hookify_rules()
            assert result.status == "fail"
            assert "0" in result.message


class TestCheckConfig:
    def test_check_config_valid(self):
        """With valid pineapple_config module, check should pass."""
        result = check_config()
        # pineapple_config.py is importable and load_config() returns defaults
        assert result.status == "pass"
        assert result.name == "config"

    def test_check_config_import_fails(self):
        """When pineapple_config fails to import, check should fail."""
        # Mock the actual import to raise
        import builtins
        original_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name == "pineapple_config":
                raise ImportError("no config module")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=failing_import):
            result = check_config()
            assert result.status == "fail"
            assert "config" in result.name.lower()


class TestCheckPipelineTools:
    def test_pipeline_tools_all_present(self, tmp_path):
        """When all expected tool files exist, check should pass."""
        for name in ["apply_pipeline.py", "pipeline_state.py", "pineapple_config.py"]:
            (tmp_path / name).write_text("# tool")

        fake_file = tmp_path / "pineapple_doctor.py"
        fake_file.write_text("")

        with patch("pineapple_doctor.__file__", str(fake_file)):
            result = check_pipeline_tools()
            assert result.status == "pass"
            assert result.name == "pipeline_tools"

    def test_pipeline_tools_some_missing(self, tmp_path):
        """When some tool files are missing, check should fail with missing names."""
        (tmp_path / "apply_pipeline.py").write_text("# tool")
        # pipeline_state.py and pineapple_config.py are missing

        fake_file = tmp_path / "pineapple_doctor.py"
        fake_file.write_text("")

        with patch("pineapple_doctor.__file__", str(fake_file)):
            result = check_pipeline_tools()
            assert result.status == "fail"
            assert "pipeline_state.py" in result.message
            assert "pineapple_config.py" in result.message

    def test_pipeline_tools_none_present(self, tmp_path):
        """When no tool files exist, check should fail listing all missing."""
        fake_file = tmp_path / "pineapple_doctor.py"
        fake_file.write_text("")

        with patch("pineapple_doctor.__file__", str(fake_file)):
            result = check_pipeline_tools()
            assert result.status == "fail"
            assert "apply_pipeline.py" in result.message


class TestRunDoctor:
    def test_run_doctor_returns_report(self):
        """run_doctor should return a DoctorReport with all checks."""
        with (
            patch("pineapple_doctor.check_docker") as mock_docker,
            patch("pineapple_doctor.check_langfuse") as mock_lf,
            patch("pineapple_doctor.check_mem0") as mock_mem0,
            patch("pineapple_doctor.check_neo4j") as mock_neo4j,
        ):
            mock_docker.return_value = CheckResult("Docker", "pass", "ok")
            mock_lf.return_value = CheckResult("LangFuse", "skip", "optional", required=False)
            mock_mem0.return_value = CheckResult("Mem0", "skip", "optional", required=False)
            mock_neo4j.return_value = CheckResult("Neo4j", "skip", "optional", required=False)
            report = run_doctor()
            assert isinstance(report, DoctorReport)
            assert len(report.checks) == 11

    def test_run_doctor_has_all_11_checks(self):
        """run_doctor should execute all 11 checks."""
        with (
            patch("pineapple_doctor.check_docker") as mock_docker,
            patch("pineapple_doctor.check_langfuse") as mock_lf,
            patch("pineapple_doctor.check_mem0") as mock_mem0,
            patch("pineapple_doctor.check_neo4j") as mock_neo4j,
        ):
            mock_docker.return_value = CheckResult("docker", "pass", "ok")
            mock_lf.return_value = CheckResult("langfuse", "skip", "optional", required=False)
            mock_mem0.return_value = CheckResult("mem0", "skip", "optional", required=False)
            mock_neo4j.return_value = CheckResult("neo4j", "skip", "optional", required=False)
            report = run_doctor()
            assert len(report.checks) == 11

    def test_run_doctor_exercises_real_checks(self):
        """run_doctor with only external services mocked exercises real check logic."""
        with (
            patch("pineapple_doctor.check_docker") as mock_docker,
            patch("pineapple_doctor.check_langfuse") as mock_lf,
            patch("pineapple_doctor.check_mem0") as mock_mem0,
            patch("pineapple_doctor.check_neo4j") as mock_neo4j,
        ):
            mock_docker.return_value = CheckResult("docker", "pass", "ok")
            mock_lf.return_value = CheckResult("langfuse", "skip", "optional", required=False)
            mock_mem0.return_value = CheckResult("mem0", "skip", "optional", required=False)
            mock_neo4j.return_value = CheckResult("neo4j", "skip", "optional", required=False)
            report = run_doctor()

            # Real checks that should have run:
            check_names = [c.name for c in report.checks]
            # check_templates runs against real templates dir
            assert "templates" in check_names
            # check_config loads real pineapple_config
            assert "config" in check_names
            # check_pipeline_tools checks real tool files
            assert "pipeline_tools" in check_names
            # check_pydantic imports real pydantic
            assert "pydantic" in check_names

            # Verify that these real checks actually passed (they should in our env)
            templates_check = next(c for c in report.checks if c.name == "templates")
            assert templates_check.status == "pass"
            config_check = next(c for c in report.checks if c.name == "config")
            assert config_check.status == "pass"
            pipeline_tools_check = next(c for c in report.checks if c.name == "pipeline_tools")
            assert pipeline_tools_check.status == "pass"
            pydantic_check = next(c for c in report.checks if c.name == "pydantic")
            assert pydantic_check.status == "pass"
