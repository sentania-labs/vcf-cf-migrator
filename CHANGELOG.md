# Changelog

## Unreleased

- M3 skeleton: `vcfcf_migrator` package on `vcf-cf-tooling-core` 0.1.0,
  `version`, `inspect`, `ui`; `tree`, `build`, `corpus-check` stubbed.
- `--source-version` (flag, environment, settings file, ui control):
  exports carry no product version, so the admin declares it; the 8.10
  floor is enforced on the declaration.
- The ui page refuses cross-origin POSTs (403).
- CI on pull requests and pushes to main; release on `vX.Y.Z` tags with
  Linux, macOS (arm64 and x86_64) and Windows binaries plus the wheel.
