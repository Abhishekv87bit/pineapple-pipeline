"""Tests for pineapple_config.py — Pineapple Pipeline Tier 2 configuration.

The module under test does not exist yet; these tests are written from spec.
All tests use tmp_path fixtures and NOT depend on any real filesystem state.
"""

import pytest
import yaml
from pathlib import Path

from pineapple_config import (
    PineappleConfig,
    ServiceConfig,
    DefaultsConfig,
    load_config,
    validate_config,
    save_config,
    _deep_merge,
)


class TestDefaultConfig:
    def test_default_config_valid(self):
        """Default config should be valid without any files."""
        config = PineappleConfig()
        assert config.version == "1.0.0"
        assert config.defaults.hookify_mode == "block"
        assert config.defaults.cost_ceiling_usd == 200.0
        assert config.defaults.wall_clock_timeout_hours == 4.0

    def test_default_services(self):
        config = PineappleConfig()
        assert "localhost:3000" in config.services.langfuse_url
        assert "localhost:7687" in config.services.neo4j_url


class TestLoadConfig:
    def test_load_no_files(self, tmp_path, monkeypatch):
        """With no config files, should return defaults."""
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: tmp_path / "nonexistent" / "config.yaml",
        )
        config = load_config(project_path=tmp_path)
        assert config.version == "1.0.0"

    def test_load_global_config(self, tmp_path, monkeypatch):
        """Global config should override defaults."""
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        global_config = global_dir / "config.yaml"
        global_config.write_text(yaml.dump({
            "defaults": {"cost_ceiling_usd": 500.0}
        }))
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: global_config,
        )
        config = load_config(project_path=tmp_path)
        assert config.defaults.cost_ceiling_usd == 500.0

    def test_load_project_overrides_global(self, tmp_path, monkeypatch):
        """Project config should override global config."""
        # Global says 500
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        global_config = global_dir / "config.yaml"
        global_config.write_text(yaml.dump({
            "defaults": {"cost_ceiling_usd": 500.0}
        }))
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: global_config,
        )
        # Project says 100
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        pineapple_dir = project_dir / ".pineapple"
        pineapple_dir.mkdir()
        (pineapple_dir / "config.yaml").write_text(yaml.dump({
            "defaults": {"cost_ceiling_usd": 100.0}
        }))
        config = load_config(project_path=project_dir)
        assert config.defaults.cost_ceiling_usd == 100.0

    def test_load_invalid_yaml_returns_defaults(self, tmp_path, monkeypatch):
        """Invalid YAML should not crash -- return defaults."""
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        global_config = global_dir / "config.yaml"
        global_config.write_text("{{not valid yaml}}")
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: global_config,
        )
        config = load_config(project_path=tmp_path)
        assert config.version == "1.0.0"  # got defaults


class TestDeepMerge:
    def test_merge_flat(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_merge_override(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_merge_nested(self):
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 3, "z": 4}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_merge_does_not_mutate(self):
        base = {"a": 1}
        override = {"b": 2}
        _deep_merge(base, override)
        assert "b" not in base


class TestValidateConfig:
    def test_default_config_no_warnings(self):
        config = PineappleConfig()
        warnings = validate_config(config)
        assert warnings == []

    def test_high_cost_ceiling_warns(self):
        config = PineappleConfig()
        config.defaults.cost_ceiling_usd = 1000.0
        warnings = validate_config(config)
        assert any("cost" in w.lower() for w in warnings)

    def test_high_concurrency_warns(self):
        config = PineappleConfig()
        config.defaults.max_concurrent_runs = 10
        warnings = validate_config(config)
        assert any("concurrent" in w.lower() or "contention" in w.lower() for w in warnings)

    def test_warn_mode_warns(self):
        config = PineappleConfig()
        config.defaults.hookify_mode = "warn"
        warnings = validate_config(config)
        assert any(
            "warn" in w.lower()
            or "prototype" in w.lower()
            or "enforced" in w.lower()
            for w in warnings
        )


class TestSaveConfig:
    def test_save_and_reload(self, tmp_path, monkeypatch):
        """Saved config should round-trip through load."""
        config_path = tmp_path / "config.yaml"
        config = PineappleConfig()
        config.defaults.cost_ceiling_usd = 42.0
        save_config(config, path=config_path)
        assert config_path.is_file()
        # Reload
        monkeypatch.setattr(
            "pineapple_config._global_config_path",
            lambda: config_path,
        )
        loaded = load_config(project_path=tmp_path)
        assert loaded.defaults.cost_ceiling_usd == 42.0
