"""Entry point for ``python -m vcfcf_migrator`` and the PyInstaller binary."""
from __future__ import annotations

import sys

from vcfcf_migrator.cli import main

if __name__ == "__main__":
    sys.exit(main())
