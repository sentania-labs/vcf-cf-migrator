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
| `vcfcf-migrator corpus-check [DIR]` | Run inspect, tree, a preview of every object and a select-all build over every zip in the corpus directory, then read the bundle back and check both halves of the contract: every document byte-identical, every rebuilt container unchanged. One line per zip. Never writes into that directory. |
| `vcfcf-migrator preview <export.zip> <object> [--out FILE] [--print]` | Write an HTML preview of one object so you can recognise it before carrying it: a dashboard laid out widget by widget, a view's columns with mock rows, a super metric's formula with its references named, an alert's symptom sets in words. One self-contained file, no network. |
| `vcfcf-migrator ui [export.zip]` | Serve the selection page on 127.0.0.1 and open the browser: the dependency tree with a checkbox per object, dependencies pulled in and labelled with what needs them, previews in place, and a Build button. Every setting and every command has a control on it; launch options (`--port`, `--no-browser`) are command line only. Ctrl-C stops it. |

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

## The preview

An export gives you a name and a uuid per object, which is not enough to
decide whether to carry a dashboard. `preview` writes one HTML file that
shows the object:

- **a dashboard** laid out widget by widget, in the columns the dashboard
  puts them in, each frame showing its title, its type and content
  appropriate to that type: an embedded view shows that view's own columns,
  a scoreboard shows its tiles, a chart shows a chart, a text widget shows
  its text;
- **a view** as the column headers it defines, with a few rows under them;
- **a super metric** as its formula, and the same formula again with every
  reference replaced by the name of the object it points at;
- **an alert** as its symptom sets stated in words, with its recommendations
  in priority order;
- every other kind as the facts its document carries.

The values are made up. They are derived by hash from the keys and names the
export already carries, so the same export previews identically on every run
and on every workstation, and two admins looking at the same object see the
same page. Names, titles, columns and keys are never invented: those come
from the export. A widget type the preview does not lay out is named rather
than drawn, and the page counts how many it met, because a Geo widget drawn
as a bar chart would be worse than one drawn as nothing.

Widget widths and order are the dashboard's own; widget heights are the
preview's, taken from the content so a table is never clipped, and every page
says so above the layout. A widget the dashboard places outside its own
declared columns widens the grid rather than being squashed into a sliver.

**No box ever says nothing.** Every widget, and every object, resolves to one
of four statements, and they look different at a glance:

1. here is the thing, drawn;
2. the export carries this, but the preview does not lay out that type, named;
3. the export carries nothing here, with what is missing said plainly;
4. the content is real and lives somewhere this page cannot follow.

State 3 beats state 2. An empty widget of a type the preview does not draw is
empty first: "carries no configuration at all" is a fact about your content,
which shows nothing on the real dashboard either and is usually an unfinished
leftover, while "this preview does not lay out Skittles" is a fact about the
tool. The same rule covers a view with no columns, a super metric with an
empty formula, an alert with no symptom sets, a group with no membership
rules and a dashboard with no widgets. The notes count the empties, so a
fourteen-widget dashboard does not have to be read box by box.

State 4 is what keeps the tool honest about what it cannot see, and it covers
more widgets than state 3 does. A widget whose column layout lives in the
saved state VCF Operations writes rather than in its configuration is
configured, in a form this page does not decode (a state whose whole value is
the empty-object marker is not a layout, and does count as empty). A widget
naming a view the export does not carry is pointing at content an export
cannot hold, since an export carries custom content only and a view shipping
with a management pack or with the product is never in one: it shows whatever
the target already has, and an export gives the tool no way to tell that apart
from a view that is genuinely gone. And a widget that drives other widgets is
the dashboard's control, so it is never told it shows nothing, whatever its
own configuration looks like.

**The preview shows the dashboard's wiring.** Most widgets on a real
dashboard do not choose their own subject: they show whatever object is
picked in the widget that feeds them, and a preview that draws each one as
though it stood alone hides the main thing about an interaction-driven
dashboard. So the layout carries the flow: a summary above the grid naming
which widget drives which, a badge and a coloured edge on each provider and
receiver, and on a receiver a line saying whose selection its values stand
for. How a widget comes by its subject has four answers, and the page gives the
right one rather than guessing: fed by a named widget; a **selector**, which
chooses no subject of its own precisely because it drives the widgets below
it; driven from **outside** the dashboard, the way a dashboard opened in an
object's context is; or waiting on a selection nothing on the dashboard
provides, which is the only one of the four that will never show data. That
last one is state 3, and the widget is still drawn under the sentence, since
a blank box would show neither its columns nor its metrics.

The file reaches nothing: inline CSS, inline SVG, no script, no font, no
image, no CDN. It opens on a workstation with no route anywhere.

## The selection page

`vcfcf-migrator ui <export.zip>` is the way through the whole job without a
command line:

- the dependency tree, grouped by kind, with a checkbox per object and each
  object's dependencies in a nested disclosure;
- checking something pulls in what it needs and says what it pulled in;
  anything pulled in is labelled with what requires it;
- unchecking something another selection still needs is refused, naming what
  needs it, because a bundle whose documents point at objects it does not
  carry is the one failure subsetting can introduce by itself;
- the preview of any object, in place on the page;
- the counts of what a build would carry, and a Build button that writes the
  bundle and says where it went;
- a control for every command (`inspect`, `tree`, `preview`, `build`,
  `corpus-check`), a text box for a selection the way `build --select` takes
  it, and a button that hands back the equivalent command line for what the
  page is set to.

It listens on 127.0.0.1 only and loads no external resource of any kind: no
script file, no font, no image, no CDN, and one inline handler that submits a
checkbox's own form, so the page works with JavaScript off and with the
keyboard alone.

A same-origin check on every POST stops another web page in your browser from
driving the port. That is a CSRF control, not an access control: any process
on the machine can reach the port while the page is running, and the page
reads and writes the paths you give it with your own rights. On the
single-user workstation this tool is for, that is the model.

Spec: `knowledge/designs/content-migrator-v1.md` in the factory repo.
