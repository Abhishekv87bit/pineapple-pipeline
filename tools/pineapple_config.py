"""
pineapple_config.py — Pineapple Pipeline configuration: typed, validated, versioned.

Config lives at ~/.pineapple/config.yaml (global) with per-project overrides
at <project>/.pineapple/config.yaml (merged, project wins).
"""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Current schema version — bump when adding/removing fields.
CURRENT_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------

class ServiceConfig(BaseModel):
    """URLs for external services."""
    langfuse_url: str = "http://localhost:3000"
    mem0_url: str = "http://localhost:8080"
    neo4j_url: str = "bolt://localhost:7687"


class DefaultsConfig(BaseModel):
    """Pipeline execution defaults."""
    max_concurrent_runs: int = 3
    wall_clock_timeout_hours: float = 4.0
    cost_ceiling_usd: float = 200.0
    hookify_mode: Literal["block", "warn"] = "block"


class PineappleConfig(BaseModel):
    """Top-level pipeline configuration."""
    version: str = CURRENT_VERSION
    services: ServiceConfig = ServiceConfig()
    defaults: DefaultsConfig = DefaultsConfig()
    template_version: str = "1.0.0"


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def _try_import_yaml():
    """Import PyYAML, returning the module or None."""
    try:
        import yaml
        return yaml
    except ImportError:
        warnings.warn(
            "PyYAML not installed — config will fall back to JSON serialisation.",
            stacklevel=3,
        )
        return None


def _global_config_path() -> Path:
    """Return the global config file path: ~/.pineapple/config.yaml."""
    return Path.home() / ".pineapple" / "config.yaml"


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return its contents as a dict.

    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    if not path.is_file():
        return {}

    yaml = _try_import_yaml()
    if yaml is None:
        logger.warning("Cannot load %s — PyYAML not available", path)
        return {}

    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to parse %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Merge / migrate helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*.

    - Override wins for leaf values.
    - Dicts are merged recursively.
    - Neither input is mutated.
    """
    merged = base.copy()
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _migrate_config(raw: dict, from_version: str) -> dict:
    """Migrate a config dict from *from_version* to CURRENT_VERSION.

    Currently a no-op stub — add migration logic here as config schema
    evolves (e.g., rename keys, backfill new defaults, drop deprecated
    sections).
    """
    logger.warning(
        "Config version %s differs from current %s — no migrations defined yet",
        from_version,
        CURRENT_VERSION,
    )
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(project_path: Path | None = None) -> PineappleConfig:
    """Load, merge, validate, and return a PineappleConfig.

    1. Load global config from ~/.pineapple/config.yaml.
    2. If *project_path* is given, load <project>/.pineapple/config.yaml and
       deep-merge (project values win).
    3. Run version migration if needed.
    4. Validate with Pydantic and return.
    """
    raw = _load_yaml(_global_config_path())

    if project_path is not None:
        project_cfg = _load_yaml(Path(project_path) / ".pineapple" / "config.yaml")
        raw = _deep_merge(raw, project_cfg)

    # Version check / migration
    raw_version = raw.get("version", CURRENT_VERSION)
    if raw_version != CURRENT_VERSION:
        raw = _migrate_config(raw, raw_version)

    return PineappleConfig(**raw)


def validate_config(config: PineappleConfig) -> list[str]:
    """Return a list of warnings about the supplied config.

    Config always loads with sane defaults, so these are advisory warnings
    rather than hard errors.
    """
    warns: list[str] = []

    if config.defaults.cost_ceiling_usd > 500:
        warns.append("Cost ceiling unusually high")

    if config.defaults.max_concurrent_runs > 5:
        warns.append("May cause resource contention")

    if config.defaults.hookify_mode == "warn":
        warns.append("Gates not enforced — prototype mode")

    return warns


def save_config(config: PineappleConfig, path: Path | None = None) -> None:
    """Persist *config* to YAML (preferred) or JSON.

    If *path* is ``None``, writes to the global config path.
    Parent directories are created automatically.
    """
    if path is None:
        path = _global_config_path()
    else:
        path = Path(path)

    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump()

    yaml = _try_import_yaml()
    if yaml is not None:
        path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    else:
        path.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )
    logger.info("Config saved to %s", path)
