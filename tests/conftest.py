from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

from make_export_fixture import build_export_zip  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """No test reads or writes the user's real settings file."""
    monkeypatch.setenv("VCFCF_MIGRATOR_CONFIG_DIR", str(tmp_path / "config-autouse"))
    monkeypatch.delenv("VCFCF_MIGRATOR_CORPUS", raising=False)


@pytest.fixture
def export_zip(tmp_path) -> Path:
    path = tmp_path / "fixture-export.zip"
    path.write_bytes(build_export_zip())
    return path


@pytest.fixture
def config_dir(tmp_path, monkeypatch) -> Path:
    """Point the settings file at a scratch directory so no test touches the
    user's real config, and clear the corpus environment override."""
    d = tmp_path / "config"
    monkeypatch.setenv("VCFCF_MIGRATOR_CONFIG_DIR", str(d))
    monkeypatch.delenv("VCFCF_MIGRATOR_CORPUS", raising=False)
    return d
