"""Persisted settings and their resolution order.

The corpus directory resolves first hit wins:

1. a value passed on the command line (``--corpus``),
2. the environment (``VCFCF_MIGRATOR_CORPUS``),
3. the settings file in the user's config directory,
4. the default: ``./corpus``, relative to the current working directory.

The corpus directory is where the admin keeps real export zips (never inside
the repo).

The config directory follows the platform convention by hand (no
platformdirs dependency): ``%APPDATA%`` on Windows, ``~/Library/Application
Support`` on macOS, ``$XDG_CONFIG_HOME`` or ``~/.config`` elsewhere, each
with a ``vcfcf-migrator`` subdirectory. ``VCFCF_MIGRATOR_CONFIG_DIR``
overrides the whole path (tests use it so nothing touches the real one).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

ENV_CORPUS = "VCFCF_MIGRATOR_CORPUS"
ENV_CONFIG_DIR = "VCFCF_MIGRATOR_CONFIG_DIR"
DEFAULT_CORPUS = "corpus"
APP_DIR_NAME = "vcfcf-migrator"
SETTINGS_FILE = "settings.json"


def config_dir() -> Path:
    """The directory the settings file lives in (not created here)."""
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE


def load_settings() -> dict:
    """The settings file as a dict; empty when absent or unreadable."""
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(values: dict) -> Path:
    """Merge *values* into the settings file and return its path."""
    merged = load_settings()
    merged.update(values)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def corpus_dir(cli_value: Optional[str] = None) -> Tuple[Path, str]:
    """Resolve the corpus directory and say where the value came from."""
    if cli_value:
        return Path(cli_value), "command line"
    env = os.environ.get(ENV_CORPUS)
    if env:
        return Path(env), f"environment ({ENV_CORPUS})"
    saved = load_settings().get("corpus_dir")
    if saved:
        return Path(str(saved)), f"settings file ({settings_path()})"
    return Path(DEFAULT_CORPUS), "default"
