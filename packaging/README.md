# Release dependencies

The release workflow installs `release-requirements.txt` with hash checking. It installs the hashed build tools from
`release-bootstrap.txt` first, then disables build isolation so source
distributions cannot download unpinned build dependencies. It installs this project without dependency resolution or build
isolation, and builds its wheel with the locked build tools. The lock covers
Python 3.12 on Linux, Windows, and both macOS architectures.

To refresh the pins after changing `pyproject.toml` or `release-requirements.in`,
install uv and run:

```sh
uv pip compile packaging/release-bootstrap.in --python-version 3.12 --generate-hashes --upgrade --output-file packaging/release-bootstrap.txt
uv pip compile pyproject.toml packaging/release-requirements.in --python-version 3.12 --universal --generate-hashes --upgrade --output-file packaging/release-requirements.txt
```

When updating the core wheel URL, update its SHA-256 fragment from the release
asset digest too. Review the lock diff and exercise the release builds on all
platforms before shipping.
