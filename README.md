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
| `vcfcf-migrator tree <export.zip> [--json]` | The dependency tree over the export's own documents. An edge pointing at something the export does not carry is shown as missing, named and counted; that is information, not an error. |
| `vcfcf-migrator build <export.zip> (--select <file> \| --select-all) --out <bundle.zip> [--json]` | Write an import bundle carrying only the closed selection. Needs a declared source version. |
| `vcfcf-migrator corpus-check [DIR]` | Run inspect, tree and a select-all build over every zip in the corpus directory, then read the bundle back and check both halves of the contract: every document byte-identical, every rebuilt container unchanged. One line per zip. Never writes into that directory. |
| `vcfcf-migrator ui [export.zip]` | Serve one local page on 127.0.0.1, open the browser. Every setting has a control on it; launch options (`--port`, `--no-browser`) are command line only. Ctrl-C stops it. |

## A worked example

Start with the tree. Every node shows its kind, name and uuid, and each
node's dependencies are indented under it:

```
$ vcfcf-migrator tree export.zip
items: dashboard=66, view=181, supermetric=99, customgroup=13, ...
dashboard Acme Capacity Overview [aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa] owner 11111111-...
  view Acme Cluster List [bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb]
    supermetric Acme Headroom After One Host Failure [cccccccc-3333-4333-8333-cccccccccccc]
      supermetric Acme Spare CPU [dddddddd-4444-4444-8444-dddddddddddd]
  view Acme Host List [eeeeeeee-5555-4555-8555-eeeeeeeeeeee]
...
edges to objects a bundle cannot carry: 201
  alert [AlertDefinition-VMWARE-SomeBuiltInAlert] wanted by notificationrule ...; it is not in this export
```

The names and uuids above are made up. The counts are from a real 9.0.2
export: 430 objects, 201 references to things a bundle from it cannot carry,
and dashboards whose closure runs from 1 object to 16.

Then pick what you want. A selection file is one object per line, `#`
comments and blank lines allowed. A bare uuid takes any object with that
uuid; `kind:uuid` narrows it; `kind:uuid@owner` picks one owner's copy of
a dashboard that two owners share. Objects the export gives no uuid
(custom groups, outbound settings) are named:

```
# one dashboard is enough; its views and their super metrics follow
dashboard:aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa
customgroup:Acme Prod Clusters
```

The build closes that selection and says what it added and why:

```
$ vcfcf-migrator --source-version 9.0.2 build export.zip \
      --select picks.txt --out bundle.zip
selected: 1 named, 11 after closure
carrying: dashboard=1, supermetric=8, view=2
pulled in by dependency: 10
  view Acme Cluster List added: required by dashboard Acme Capacity Overview
  supermetric Acme Headroom After One Host Failure added: required by view Acme Cluster List
  ...
bundle: bundle.zip
```

`vcfcf-migrator inspect bundle.zip` lists exactly those 11 items. Import
the bundle into the target the way you would import any content export.

The edges the tree follows, and how the export writes each:

| From | To | Written as |
|---|---|---|
| dashboard | view | uuid, widget `viewDefinitionId` |
| dashboard | super metric | any string in the widget subtree, by uuid or by name |
| dashboard | custom group | widget scope or `entryKeys.resource`, by name, in any of its shapes |
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

By name matters as much as by uuid: an 8.x super metric formula names another
one as `Super Metric|@supermetric:"<Name>"`, and an export gives custom
groups no uuid at all. A name that several different objects answer to
carries every match and says so, in both `tree` and `build`.

Two things worth knowing about what comes out. Each object's document is
copied into the bundle as the bytes the export held, never re-rendered
from a parsed model, so a field this tool has never heard of cannot be
dropped. And a member whose content the tool does not understand
(policies, users, roles, cost drivers) is never carried: it cannot be
selected, and the build report names every one it left behind.

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
