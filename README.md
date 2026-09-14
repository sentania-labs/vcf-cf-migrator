# vcf-cf-migrator

Move custom content between VCF Operations instances from a content
export zip: see the dependency tree, preview each item with mock data,
pick what to keep, and get an import bundle the target accepts. Runs on
a workstation (Mac, Windows, Linux). Offline, no credentials, no LLM.

Built on [`vcf-cf-tooling-core`](https://github.com/sentania-labs/vcf-content-factory/releases?q=core-v),
the library carved out of the VCF Content Factory.

## Install

Either download the binary for your OS from the
[latest release](https://github.com/sentania-labs/vcf-cf-migrator/releases/latest):
`vcfcf-migrator-linux`, `vcfcf-migrator-macos-arm64` (Apple Silicon),
`vcfcf-migrator-macos-x86_64` (Intel), `vcfcf-migrator-windows.exe`;
or install the wheel with Python 3.9 or later:

```
pip install https://github.com/sentania-labs/vcf-cf-migrator/releases/download/vX.Y.Z/vcf_cf_migrator-X.Y.Z-py3-none-any.whl
```

The wheel pulls the pinned core library from its own GitHub Release.

First run of a downloaded binary:

- Linux and macOS: `chmod +x vcfcf-migrator-*` (release assets carry no execute bit).
- macOS: the binary is unsigned, so Gatekeeper blocks it once. Either
  `xattr -d com.apple.quarantine vcfcf-migrator-macos-*` or allow it under
  System Settings, Privacy & Security, after the first refusal.
- Windows: SmartScreen shows "Windows protected your PC"; choose More info,
  then Run anyway.

## Commands

| Command | What it does |
|---|---|
| `vcfcf-migrator version` | Print the tool and core library versions. |
| `vcfcf-migrator inspect <export.zip> [--json]` | List every content item in the export by type, uuid and name; anything unrecognised is listed as carried, not inspected. |
| `vcfcf-migrator tree <export.zip>` | Dependency tree (M4, not implemented yet). |
| `vcfcf-migrator build <export.zip> --select <file> --out <bundle.zip>` | Write an import bundle from a selection (M4, not implemented yet). |
| `vcfcf-migrator corpus-check` | Run inspect, tree and build over every zip in the corpus directory (M5, not implemented yet). |
| `vcfcf-migrator ui [export.zip]` | Serve one local page on 127.0.0.1, open the browser. Every setting has a control on it; launch options (`--port`, `--no-browser`) are command line only. Ctrl-C stops it. |

## Settings

Both are global options, so they go before the command
(`vcfcf-migrator --corpus DIR --source-version 8.18.7 ui`). Each resolves
in the same order: the flag, then the environment variable, then the
settings file the `ui` page writes into your user config directory
(`VCFCF_MIGRATOR_CONFIG_DIR` overrides where), then the default.

- **Source version** (`--source-version X.Y[.Z]`, `VCFCF_MIGRATOR_SOURCE_VERSION`):
  the VCF Operations version the export came from. Exports carry no
  product version, so you declare it. Anything below 8.10 is refused.
  With nothing declared, `inspect` says so and continues; `build` refuses.
- **Corpus directory** (`--corpus DIR`, `VCFCF_MIGRATOR_CORPUS`, default
  `./corpus`): where real export zips live on your workstation, never in
  this repo.

Spec: `knowledge/designs/content-migrator-v1.md` in the factory repo.
