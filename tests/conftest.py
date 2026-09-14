from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

from make_export_fixture import build_export_zip  # noqa: E402


@pytest.fixture
def export_zip(tmp_path) -> Path:
    path = tmp_path / "fixture-export.zip"
    path.write_bytes(build_export_zip())
    return path


@pytest.fixture
def old_export_zip(tmp_path) -> Path:
    path = tmp_path / "fixture-export-8.5.zip"
    path.write_bytes(build_export_zip(version="8.5.0"))
    return path


@pytest.fixture
def config_dir(tmp_path, monkeypatch) -> Path:
    """Point the settings file at a scratch directory so no test touches the
    user's real config, and clear the corpus environment override."""
    d = tmp_path / "config"
    monkeypatch.setenv("VCFCF_MIGRATOR_CONFIG_DIR", str(d))
    monkeypatch.delenv("VCFCF_MIGRATOR_CORPUS", raising=False)
    return d
