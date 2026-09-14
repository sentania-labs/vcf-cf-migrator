"""vcf-cf-migrator: move custom VCF Operations content between instances.

``__version__`` is whatever distribution metadata ``importlib.metadata`` can
see for ``vcf-cf-migrator``: the wheel's version (from a ``vX.Y.Z`` tag, or a
dev version on an untagged tree). ``0.0.0`` when no metadata is visible.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("vcf-cf-migrator")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
