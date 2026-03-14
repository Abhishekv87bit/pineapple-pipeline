"""
Tests for the production pipeline templates themselves.

Validates that:
- All placeholders in templates correspond to real config keys
- Rendered templates are syntactically valid (Python, YAML)
- No unreplaced placeholders remain after rendering
- Known issues (e.g., rate_limiter default) are caught
"""

import ast
import re
import sys
from pathlib import Path

# Make the tools/ directory importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import pytest
from apply_pipeline import detect_project, fill_template

# ── Paths ──
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# ── Helpers ──

# Regex to find all {{WORD}} placeholders in template text
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")

# Known placeholders that appear inside template content as literal strings
# (not intended to be replaced by apply_pipeline). These are payload data,
# not pipeline placeholders.
CONTENT_PLACEHOLDERS = {
    # test_adversarial.py uses {{SYSTEM_PROMPT}} as an injection test payload
    "SYSTEM_PROMPT",
}


def get_all_templates() -> list[Path]:
    """Return all template files in the templates directory."""
    templates = []
    for ext in ("*.py", "*.yml", "*.yaml"):
        templates.extend(TEMPLATE_DIR.glob(ext))
    # Also include Dockerfiles and other template files
    for f in TEMPLATE_DIR.iterdir():
        if f.is_file() and f not in templates:
            templates.append(f)
    return sorted(set(templates))


def get_py_templates() -> list[Path]:
    """Return all .py template files."""
    return sorted(TEMPLATE_DIR.glob("*.py"))


def get_yml_templates() -> list[Path]:
    """Return all .yml/.yaml template files."""
    result = list(TEMPLATE_DIR.glob("*.yml"))
    result.extend(TEMPLATE_DIR.glob("*.yaml"))
    return sorted(result)


def get_dockerfile_templates() -> list[Path]:
    """Return all Dockerfile templates."""
    return sorted(p for p in TEMPLATE_DIR.iterdir() if p.name.startswith("Dockerfile"))


def get_default_config(tmp_path: Path = None) -> dict:
    """Get the default config from detect_project with a dummy path."""
    dummy = tmp_path or Path("/tmp/test-project")
    return detect_project(dummy)


# ═══════════════════════════════════════════════════════════════
# Placeholder coverage
# ═══════════════════════════════════════════════════════════════


class TestPlaceholderCoverage:
    """Ensure every placeholder used in templates has a matching config key."""

    def test_all_placeholders_have_config_keys(self, tmp_path: Path):
        """Scan every template for {{WORD}} patterns and verify each
        exists as a key in detect_project() defaults.

        This catches typos like {{DEFAUT_LIMIT}} or {{BACKEN_DIR}}.
        """
        config = get_default_config(tmp_path)
        config_keys = set(config.keys())

        # Also include PORT which is used in Dockerfiles but maps to
        # BACKEND_PORT/FRONTEND_PORT -- this is a known gap, so we
        # track it but don't add it to config_keys
        missing = {}

        for template_path in get_all_templates():
            text = template_path.read_text(encoding="utf-8", errors="ignore")
            placeholders = set(PLACEHOLDER_RE.findall(text))

            # Filter out known content placeholders (not meant to be replaced)
            placeholders -= CONTENT_PLACEHOLDERS

            for placeholder in placeholders:
                if placeholder not in config_keys:
                    missing.setdefault(placeholder, []).append(template_path.name)

        if missing:
            lines = []
            for placeholder, files in sorted(missing.items()):
                lines.append(f"  {{{{{{placeholder}}}}}} used in: {', '.join(files)}")
            detail = "\n".join(lines)
            pytest.fail(
                f"Placeholders used in templates but missing from detect_project() config:\n"
                f"{detail}\n\n"
                f"Available config keys: {sorted(config_keys)}"
            )


# ═══════════════════════════════════════════════════════════════
# Syntax validation
# ═══════════════════════════════════════════════════════════════


class TestRenderedTemplateSyntax:
    """Verify that rendered templates are syntactically valid."""

    @pytest.mark.parametrize(
        "template_path",
        get_py_templates(),
        ids=lambda p: p.name,
    )
    def test_rendered_templates_valid_python(self, template_path: Path, tmp_path: Path):
        """Render each .py template with default config and verify it parses."""
        config = get_default_config(tmp_path)
        raw = template_path.read_text(encoding="utf-8")
        rendered = fill_template(raw, config)

        try:
            ast.parse(rendered, filename=template_path.name)
        except SyntaxError as e:
            pytest.fail(
                f"Rendered {template_path.name} has a Python syntax error:\n"
                f"  Line {e.lineno}: {e.msg}\n"
                f"  Text: {e.text}"
            )

    @pytest.mark.parametrize(
        "template_path",
        get_yml_templates(),
        ids=lambda p: p.name,
    )
    def test_rendered_templates_valid_yaml(self, template_path: Path, tmp_path: Path):
        """Render each .yml template with default config and verify YAML parses."""
        yaml = pytest.importorskip("yaml", reason="PyYAML required for YAML validation")

        config = get_default_config(tmp_path)
        raw = template_path.read_text(encoding="utf-8")
        rendered = fill_template(raw, config)

        try:
            result = yaml.safe_load(rendered)
        except yaml.YAMLError as e:
            pytest.fail(
                f"Rendered {template_path.name} has a YAML syntax error:\n{e}"
            )

        # Sanity: should parse to a non-empty dict
        assert result is not None, f"{template_path.name} rendered to empty YAML"
        assert isinstance(result, dict), (
            f"{template_path.name} rendered to {type(result).__name__}, expected dict"
        )


# ═══════════════════════════════════════════════════════════════
# Known issues
# ═══════════════════════════════════════════════════════════════


class TestKnownIssues:
    """Tests that specifically target known template bugs."""

    def test_rate_limiter_default_not_placeholder(self, tmp_path: Path):
        """The rendered rate_limiter.py should have the actual limit value
        in the function default, NOT the literal string '{{DEFAULT_LIMIT}}'.

        Known issue: line 23 has default_limit: str = "{{DEFAULT_LIMIT}}"
        which means the raw template would use a placeholder literal as a
        Python default. After rendering, it should be the resolved value.
        """
        config = get_default_config(tmp_path)
        template_path = TEMPLATE_DIR / "rate_limiter.py"
        raw = template_path.read_text(encoding="utf-8")
        rendered = fill_template(raw, config)

        # The rendered output should contain the actual limit, not the placeholder
        assert '{{DEFAULT_LIMIT}}' not in rendered, (
            "rate_limiter.py still contains {{DEFAULT_LIMIT}} after rendering"
        )

        # Specifically check the function signature has the real value
        assert '"60/minute"' in rendered or "'60/minute'" in rendered, (
            "rate_limiter.py function default should be '60/minute' after rendering, "
            f"but got: {[l for l in rendered.splitlines() if 'default_limit' in l]}"
        )


# ═══════════════════════════════════════════════════════════════
# Unreplaced placeholder sweep
# ═══════════════════════════════════════════════════════════════


class TestNoUnreplacedPlaceholders:
    """After rendering with default config, no {{ should remain (except known content)."""

    def test_no_template_has_unreplaced_placeholders(self, tmp_path: Path):
        """Render ALL templates with default config, assert no {{ remains.

        Excludes known content placeholders (e.g., {{SYSTEM_PROMPT}} in
        test_adversarial.py which is an injection test payload, not a
        pipeline placeholder).
        """
        config = get_default_config(tmp_path)
        failures = []

        for template_path in get_all_templates():
            raw = template_path.read_text(encoding="utf-8", errors="ignore")
            rendered = fill_template(raw, config)

            # Find any remaining {{ }} patterns
            remaining = PLACEHOLDER_RE.findall(rendered)

            # Filter out known content placeholders
            real_remaining = [p for p in remaining if p not in CONTENT_PLACEHOLDERS]

            if real_remaining:
                placeholders_str = ", ".join(f"{{{{{p}}}}}" for p in real_remaining)
                failures.append(f"  {template_path.name}: {placeholders_str}")

        if failures:
            detail = "\n".join(failures)
            pytest.fail(
                f"Templates with unreplaced placeholders after rendering:\n{detail}"
            )


# ═══════════════════════════════════════════════════════════════
# Dockerfile structure
# ═══════════════════════════════════════════════════════════════


class TestDockerfileStructure:
    """Verify rendered Dockerfiles have required instructions."""

    @pytest.mark.parametrize(
        "template_path",
        get_dockerfile_templates(),
        ids=lambda p: p.name,
    )
    def test_dockerfile_has_from_and_cmd(self, template_path: Path, tmp_path: Path):
        """Every Dockerfile must have at least one FROM and one CMD or ENTRYPOINT."""
        config = get_default_config(tmp_path)
        raw = template_path.read_text(encoding="utf-8")
        rendered = fill_template(raw, config)

        lines = rendered.upper().splitlines()
        # Strip comments for checking
        instruction_lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

        has_from = any(line.startswith("FROM ") for line in instruction_lines)
        has_cmd = any(
            line.startswith("CMD ") or line.startswith("ENTRYPOINT ")
            for line in instruction_lines
        )

        assert has_from, f"{template_path.name} is missing FROM instruction"
        assert has_cmd, f"{template_path.name} is missing CMD/ENTRYPOINT instruction"

    @pytest.mark.parametrize(
        "template_path",
        get_dockerfile_templates(),
        ids=lambda p: p.name,
    )
    def test_dockerfile_has_expose(self, template_path: Path, tmp_path: Path):
        """Dockerfiles should EXPOSE a port."""
        config = get_default_config(tmp_path)
        raw = template_path.read_text(encoding="utf-8")
        rendered = fill_template(raw, config)

        assert "EXPOSE" in rendered.upper(), (
            f"{template_path.name} is missing EXPOSE instruction"
        )


# ═══════════════════════════════════════════════════════════════
# Template file inventory
# ═══════════════════════════════════════════════════════════════


class TestTemplateInventory:
    """Ensure expected templates exist and are non-empty."""

    EXPECTED_TEMPLATES = [
        "Dockerfile.fastapi",
        "Dockerfile.vite",
        "docker-compose.template.yml",
        "ci.github-actions.yml",
        "env.template",
        "rate_limiter.py",
        "input_guardrails.py",
        "observability.py",
        "resilience.py",
        "cache.py",
        "mcp_server.py",
    ]

    @pytest.mark.parametrize("name", EXPECTED_TEMPLATES)
    def test_template_exists_and_nonempty(self, name: str):
        """Each expected template should exist and contain content."""
        path = TEMPLATE_DIR / name
        assert path.is_file(), f"Template {name} not found in {TEMPLATE_DIR}"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 10, f"Template {name} is suspiciously short ({len(content)} chars)"
