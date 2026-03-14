"""
Tests for apply_pipeline.py — the Pineapple Pipeline's core scaffolding tool.

Covers: detect_project, fill_template, apply_file, apply_pipeline
All tests use real file I/O via tmp_path (no mocks).
"""

import sys
from pathlib import Path

# Make the tools/ directory importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import pytest
from apply_pipeline import detect_project, fill_template, apply_file, apply_pipeline


# ═══════════════════════════════════════════════════════════════
# detect_project
# ═══════════════════════════════════════════════════════════════


class TestDetectProject:
    """Tests for auto-detection of project configuration."""

    def test_detect_project_standard_layout(self, tmp_path: Path):
        """Standard FastAPI layout: backend/app/main.py with app = FastAPI()."""
        main_py = tmp_path / "backend" / "app" / "main.py"
        main_py.parent.mkdir(parents=True)
        main_py.write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
        )

        config = detect_project(tmp_path)

        assert config["PROJECT_NAME"] == tmp_path.name
        assert config["BACKEND_DIR"] == "backend"
        assert config["APP_MODULE"] == "app.main:app"
        assert config["BACKEND_PORT"] == "8000"  # default
        assert config["PYTHON_VERSION"] == "3.12"
        assert config["DEFAULT_LIMIT"] == "60/minute"

    def test_detect_project_no_backend(self, tmp_path: Path):
        """Empty project dir should use all defaults."""
        config = detect_project(tmp_path)

        assert config["PROJECT_NAME"] == tmp_path.name
        assert config["BACKEND_DIR"] == "backend"
        assert config["FRONTEND_DIR"] == "frontend"
        assert config["BACKEND_PORT"] == "8000"
        assert config["FRONTEND_PORT"] == "3000"
        assert config["APP_MODULE"] == "app.main:app"
        assert config["TEST_COMMAND"] == "pytest"
        assert config["ENV_FILE"] == ".env"
        assert config["DB_PATH"] == "/data"
        assert config["EXTRA_SYSTEM_DEPS"] == ""
        assert config["EXTRA_PIP_DEPS"] == ""

    def test_detect_project_custom_port(self, tmp_path: Path):
        """Project with .env containing PORT=9000 should detect custom port."""
        backend = tmp_path / "backend" / "app"
        backend.mkdir(parents=True)
        (backend / "main.py").write_text("app = FastAPI()\n")

        env_file = tmp_path / "backend" / ".env"
        env_file.write_text("PORT=9000\nDEBUG=true\n")

        config = detect_project(tmp_path)

        assert config["BACKEND_PORT"] == "9000"

    def test_detect_project_server_dir(self, tmp_path: Path):
        """Backend in 'server/' instead of 'backend/'."""
        server_app = tmp_path / "server" / "app" / "main.py"
        server_app.parent.mkdir(parents=True)
        server_app.write_text("myapp = FastAPI()\n")

        config = detect_project(tmp_path)

        assert config["BACKEND_DIR"] == "server"
        assert config["APP_MODULE"] == "app.main:myapp"

    def test_detect_project_frontend(self, tmp_path: Path):
        """Frontend detected via package.json."""
        fe = tmp_path / "client" / "package.json"
        fe.parent.mkdir(parents=True)
        fe.write_text('{"name": "frontend"}')

        config = detect_project(tmp_path)

        assert config["FRONTEND_DIR"] == "client"

    def test_detect_project_config_py_port(self, tmp_path: Path):
        """Port detected from app/config.py."""
        backend = tmp_path / "backend" / "app"
        backend.mkdir(parents=True)
        (backend / "main.py").write_text("app = FastAPI()\n")
        (backend / "config.py").write_text("PORT = 7777\n")

        config = detect_project(tmp_path)

        assert config["BACKEND_PORT"] == "7777"


# ═══════════════════════════════════════════════════════════════
# fill_template
# ═══════════════════════════════════════════════════════════════


class TestFillTemplate:
    """Tests for placeholder replacement."""

    def test_fill_template_all_placeholders_replaced(self):
        """All known placeholders should be replaced; no {{ remains."""
        template = (
            "name: {{PROJECT_NAME}}\n"
            "port: {{BACKEND_PORT}}\n"
            "python: {{PYTHON_VERSION}}\n"
            "default_limit: {{DEFAULT_LIMIT}}\n"
        )
        config = detect_project(Path("/tmp/fake-project"))

        result = fill_template(template, config)

        assert "{{" not in result
        assert "/tmp/fake-project" not in result  # name is just the dir name
        assert "fake-project" in result
        assert "8000" in result
        assert "3.12" in result
        assert "60/minute" in result

    def test_fill_template_unknown_placeholder_survives(self):
        """Unknown placeholders should NOT be replaced -- they survive as-is.

        This is the documented behavior (and arguably a bug): fill_template
        only replaces keys present in config. A typo like {{TYPO}} passes
        through silently.
        """
        template = "limit: {{DEFAULT_LIMIT}}, broken: {{TYPO}}"
        config = {"DEFAULT_LIMIT": "60/minute"}

        result = fill_template(template, config)

        assert result == "limit: 60/minute, broken: {{TYPO}}"
        assert "{{TYPO}}" in result

    def test_fill_template_multiple_occurrences(self):
        """Same placeholder used twice should be replaced in both spots."""
        template = "FROM python:{{PYTHON_VERSION}}-slim\nRUN python{{PYTHON_VERSION}}"
        config = {"PYTHON_VERSION": "3.12"}

        result = fill_template(template, config)

        assert result == "FROM python:3.12-slim\nRUN python3.12"
        assert "{{PYTHON_VERSION}}" not in result

    def test_fill_template_empty_value(self):
        """Empty string config values should result in the placeholder being removed."""
        template = "deps: {{EXTRA_SYSTEM_DEPS}}\n"
        config = {"EXTRA_SYSTEM_DEPS": ""}

        result = fill_template(template, config)

        assert result == "deps: \n"
        assert "{{" not in result


# ═══════════════════════════════════════════════════════════════
# apply_file
# ═══════════════════════════════════════════════════════════════


class TestApplyFile:
    """Tests for single-template application."""

    @pytest.fixture
    def default_config(self, tmp_path: Path) -> dict:
        return detect_project(tmp_path)

    def test_apply_file_creates_file(self, tmp_path: Path, default_config: dict):
        """Applying a template should create the destination file."""
        dest = tmp_path / "output" / "rate_limiter.py"

        result = apply_file("rate_limiter.py", dest, default_config)

        assert dest.is_file()
        assert "CREATED" in result
        content = dest.read_text()
        assert "def setup_rate_limiting" in content

    def test_apply_file_skip_existing(self, tmp_path: Path, default_config: dict):
        """Existing file should not be overwritten without --force."""
        dest = tmp_path / "existing.py"
        dest.write_text("original content")

        result = apply_file("rate_limiter.py", dest, default_config, force=False)

        assert "SKIP" in result
        assert dest.read_text() == "original content"

    def test_apply_file_force_overwrite(self, tmp_path: Path, default_config: dict):
        """--force should overwrite existing files."""
        dest = tmp_path / "existing.py"
        dest.write_text("original content")

        result = apply_file("rate_limiter.py", dest, default_config, force=True)

        assert "CREATED" in result
        content = dest.read_text()
        assert content != "original content"
        assert "setup_rate_limiting" in content

    def test_apply_file_dry_run(self, tmp_path: Path, default_config: dict):
        """Dry run should not create any files."""
        dest = tmp_path / "output" / "rate_limiter.py"

        result = apply_file("rate_limiter.py", dest, default_config, dry_run=True)

        assert "WOULD CREATE" in result
        assert not dest.exists()

    def test_apply_file_missing_template(self, tmp_path: Path, default_config: dict):
        """Missing template should be skipped gracefully."""
        dest = tmp_path / "output" / "nonexistent.py"

        result = apply_file("does_not_exist.py", dest, default_config)

        assert "SKIP" in result
        assert "not found" in result
        assert not dest.exists()

    def test_apply_file_creates_parent_dirs(self, tmp_path: Path, default_config: dict):
        """apply_file should create parent directories if they don't exist."""
        dest = tmp_path / "deep" / "nested" / "dir" / "file.py"

        apply_file("rate_limiter.py", dest, default_config)

        assert dest.is_file()


# ═══════════════════════════════════════════════════════════════
# apply_pipeline (full scaffold)
# ═══════════════════════════════════════════════════════════════


class TestApplyPipeline:
    """Tests for full pipeline scaffolding."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        """Create a minimal project structure for testing."""
        backend = tmp_path / "backend" / "app"
        backend.mkdir(parents=True)
        (backend / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
        )
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name": "test-frontend"}')
        return tmp_path

    def test_apply_pipeline_creates_expected_structure(self, project: Path):
        """fastapi-vite stack should create all expected files."""
        apply_pipeline(project, stack="fastapi-vite")

        # Docker
        assert (project / "backend" / "Dockerfile").is_file()
        assert (project / "frontend" / "Dockerfile").is_file()
        assert (project / "docker-compose.yml").is_file()

        # CI
        assert (project / ".github" / "workflows" / "ci.yml").is_file()

        # Environment
        assert (project / ".env.example").is_file()

        # Middleware
        middleware = project / "backend" / "app" / "middleware"
        assert (middleware / "rate_limiter.py").is_file()
        assert (middleware / "input_guardrails.py").is_file()
        assert (middleware / "observability.py").is_file()
        assert (middleware / "resilience.py").is_file()
        assert (middleware / "cache.py").is_file()

        # MCP
        assert (project / "backend" / "mcp_server.py").is_file()

        # Test templates
        assert (project / "backend" / "tests" / "test_adversarial.py").is_file()
        assert (project / "backend" / "tests" / "test_eval_benchmark.py").is_file()

        # Project scaffolding
        assert (project / ".gitignore").is_file()
        assert (project / ".mcp.json").is_file()
        assert (project / "CLAUDE.md").is_file()
        assert (project / ".pineapple").is_dir()
        assert (project / "memory" / "MEMORY.md").is_file()
        assert any((project / "projects").glob("*-bible.yaml"))

    def test_apply_pipeline_dry_run_no_files(self, project: Path):
        """Dry run should not create any pipeline files."""
        # Snapshot existing files before dry run
        before = set()
        for p in project.rglob("*"):
            if p.is_file():
                before.add(p)

        apply_pipeline(project, stack="fastapi-vite", dry_run=True)

        # Check no new files were created
        after = set()
        for p in project.rglob("*"):
            if p.is_file():
                after.add(p)

        new_files = after - before
        assert len(new_files) == 0, f"Dry run created files: {new_files}"

    def test_apply_pipeline_idempotent(self, project: Path):
        """Running apply_pipeline twice should produce the same result."""
        apply_pipeline(project, stack="fastapi-vite")

        # Collect all file contents after first run
        first_run = {}
        for p in project.rglob("*"):
            if p.is_file():
                first_run[p.relative_to(project)] = p.read_text(encoding="utf-8", errors="ignore")

        # Run again (without --force, so existing files get SKIPped)
        apply_pipeline(project, stack="fastapi-vite")

        # Collect after second run
        second_run = {}
        for p in project.rglob("*"):
            if p.is_file():
                second_run[p.relative_to(project)] = p.read_text(encoding="utf-8", errors="ignore")

        assert first_run == second_run

    def test_apply_pipeline_fastapi_only_stack(self, project: Path):
        """fastapi-only stack should NOT create frontend Dockerfile or docker-compose."""
        apply_pipeline(project, stack="fastapi-only")

        # Backend Dockerfile should exist
        assert (project / "backend" / "Dockerfile").is_file()

        # Frontend Dockerfile and docker-compose should NOT exist
        assert not (project / "frontend" / "Dockerfile").is_file()
        assert not (project / "docker-compose.yml").is_file()

        # CI and middleware should still exist
        assert (project / ".github" / "workflows" / "ci.yml").is_file()
        assert (project / "backend" / "app" / "middleware" / "rate_limiter.py").is_file()

    def test_apply_pipeline_vite_only_stack(self, tmp_path: Path):
        """vite-only stack should NOT create backend files."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name": "test"}')

        apply_pipeline(tmp_path, stack="vite-only")

        # Frontend Dockerfile should exist
        assert (tmp_path / "frontend" / "Dockerfile").is_file()

        # Backend Dockerfile should NOT exist
        assert not (tmp_path / "backend" / "Dockerfile").is_file()
        # No middleware
        assert not (tmp_path / "backend" / "app" / "middleware").exists()
        # No docker-compose (only for fastapi-vite)
        assert not (tmp_path / "docker-compose.yml").is_file()

    def test_apply_pipeline_gitignore_has_env(self, project: Path):
        """.gitignore should contain .env after pipeline runs."""
        apply_pipeline(project, stack="fastapi-vite")

        gitignore = project / ".gitignore"
        assert gitignore.is_file()
        assert ".env" in gitignore.read_text()

    def test_apply_pipeline_existing_gitignore_preserved(self, project: Path):
        """Existing .gitignore content should be preserved."""
        gitignore = project / ".gitignore"
        gitignore.write_text("node_modules/\n__pycache__/\n")

        apply_pipeline(project, stack="fastapi-vite")

        content = gitignore.read_text()
        assert "node_modules/" in content
        assert "__pycache__/" in content
        assert ".env" in content

    def test_apply_pipeline_mcp_json_content(self, project: Path):
        """Generated .mcp.json should reference the backend correctly."""
        import json

        apply_pipeline(project, stack="fastapi-vite")

        mcp_json = project / ".mcp.json"
        assert mcp_json.is_file()

        data = json.loads(mcp_json.read_text())
        assert "mcpServers" in data
        # The server name should incorporate the project name
        servers = data["mcpServers"]
        assert len(servers) > 0
        for server_config in servers.values():
            assert "command" in server_config
            assert "args" in server_config
