# vcf-cf-migrator

Move custom content between VCF Operations instances from a content
export zip: see the dependency tree, preview each item with mock data,
pick what to keep, and get an import bundle the target accepts. Runs on
a workstation (Mac, Windows, Linux). Offline, no credentials, no LLM.

Built on [`vcf-cf-tooling-core`](https://github.com/sentania-labs/vcf-content-factory/releases?q=core-v),
the library carved out of the VCF Content Factory.

## Install

Either download the binary for your OS from the
[latest release](https://github.com/sentania-labs/vcf-cf-migrator/releases/latest)
(`vcfcf-migrator-linux`, `vcfcf-migrator-macos`, `vcfcf-migrator-windows.exe`),
or install the wheel with Python 3.9 or later:

```
pip install https://github.com/sentania-labs/vcf-cf-migrator/releases/download/vX.Y.Z/vcf_cf_migrator-X.Y.Z-py3-none-any.whl
```

The wheel pulls the pinned core library from its own GitHub Release.

## Commands

| Command | What it does |
|---|---|
| `vcfcf-migrator version` | Print the tool and core library versions. |
| `vcfcf-migrator inspect <export.zip> [--json]` | List every content item in the export by type, uuid and name; anything unrecognised is listed as carried, not inspected. Exports that identify as older than 8.10 are refused. |
| `vcfcf-migrator tree <export.zip>` | Dependency tree (M4, not implemented yet). |
| `vcfcf-migrator build <export.zip> --select <file> --out <bundle.zip>` | Write an import bundle from a selection (M4, not implemented yet). |
| `vcfcf-migrator corpus-check` | Run inspect, tree and build over every zip in the corpus directory (M5, not implemented yet). |
| `vcfcf-migrator ui [export.zip]` | Serve one local page on 127.0.0.1, open the browser. Every command line option has a control on it. Ctrl-C stops it. |

## Corpus directory

Real export zips stay on your workstation, never in this repo. The tool
looks for them in `./corpus` by default. Change that with `--corpus DIR`,
the `VCFCF_MIGRATOR_CORPUS` environment variable, or the settings control
on the `ui` page, which saves to a small settings file in your user config
directory (`VCFCF_MIGRATOR_CONFIG_DIR` overrides where).

Spec: `knowledge/designs/content-migrator-v1.md` in the factory repo.
