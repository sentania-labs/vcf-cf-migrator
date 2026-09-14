# Changelog

## Unreleased

- `tree`: the dependency tree over the export's own documents, readable by
  default and `--json` for machines. Every reference is read from the parsed
  document, never with a regex over its text, because one reference class is
  written in several shapes and a regex matches one of them. The edges:

  | From | To | Written as |
  |---|---|---|
  | dashboard | view | uuid, widget `viewDefinitionId` |
  | dashboard | super metric | widget metric key, by uuid or by name |
  | dashboard | custom group | widget resource scope, by name, object or list shaped |
  | view | super metric | column `attributeKey`, by uuid or by name |
  | super metric | super metric | the `formula` field, by uuid or by name |
  | symptom | super metric | `<Condition key=...>`, by uuid or by name |
  | alert | symptom, recommendation | uuid, `ref=` |
  | custom group | custom group | membership `RelationshipRule`, by name |
  | custom group | policy | uuid; always reported missing, since `policies.xml` is never carried |
  | notification rule | alert | uuid, condition `AlertDefinitionID` |
  | notification rule | custom group | condition resource scope, by name |
  | notification rule | outbound endpoint, template | by name |
  | report | view, dashboard | uuid, section `ContentKey` |

  An edge whose target is not in the export is shown as missing, named and
  counted; a by-name value that names no object here, which is the normal case
  for a membership rule or a resource scope, is not. A name several different
  objects answer to carries every match and says so, in `tree` and in `build`.
  A uuid quoted in a description is prose, not a reference, and is not
  followed.
- `build`: an import bundle carrying only the closed selection.
  `--select FILE` takes uuid or `kind:uuid` lines (`kind:uuid@owner` for a
  dashboard two owners share), `--select-all` takes everything. Selecting
  a node pulls in what it depends on, and the output says what was added
  and why. A line naming something the export does not carry is refused
  with exit 1 and no bundle on disk.
- Each carried object's document is copied into the bundle as the bytes
  the export held; only containers are rebuilt. `configuration.json` is
  written fresh with the counts actually carried, and `usermappings.json`
  and `dashboardsharings/<owner>` are narrowed to what the bundle holds.
  Members the tool does not understand are never carried, and the report
  names them.
- `corpus-check`: inspect, tree and a select-all build over every zip in
  the corpus directory, then the bundle read back and compared with the
  source document by document and container by container. One line per zip, exit non-zero only on an
  error. It never writes into the corpus directory and never guesses a
  source version (read-only `versions.json` beside the zips, or
  `--source-version`).
- The ui page keeps working; its controls for the three new commands land
  in the next change, and until then their buttons hand back the exact
  command line for what the page is set to.
- M3 skeleton: `vcfcf_migrator` package on `vcf-cf-tooling-core` 0.1.0,
  `version`, `inspect`, `ui`; `tree`, `build`, `corpus-check` stubbed.
- `--source-version` (flag, environment, settings file, ui control):
  exports carry no product version, so the admin declares it; the 8.10
  floor is enforced on the declaration.
- The ui page refuses cross-origin POSTs (403).
- CI on pull requests and pushes to main; release on `vX.Y.Z` tags with
  Linux, macOS (arm64 and x86_64) and Windows binaries plus the wheel.
