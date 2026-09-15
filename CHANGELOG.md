# Changelog

## Unreleased

- `preview`: an HTML page per object, so an admin can recognise it before
  deciding to carry it. A dashboard is laid out widget by widget in the
  columns the dashboard puts them in, each frame showing its title, its type
  and content appropriate to that type (an embedded view shows that view's
  columns, a scoreboard its tiles, a chart a chart, a text widget its text).
  A view shows the column headers it defines with mock rows under them. A
  super metric shows its formula, and the formula again with every reference
  replaced by the name of the object it points at. An alert states its
  symptom sets in words with its recommendations in priority order. Every
  other kind states the facts its document carries.

  Thirteen widget types are laid out on purpose; the list lives in
  `preview.WIDGET_RENDERERS` and nowhere else, and the fixture carries one
  widget of every one of them, so a new renderer fails the suite until
  something exercises it. Every other type is named rather than drawn, and the
  page counts how many it met: drawing a Geo widget as a bar chart would be
  worse than drawing nothing, because the admin would believe it. The same
  applies to a view whose presentation is not a list: its type is stated, its
  attributes listed, and no donut is drawn over buckets this tool does not
  read.

  **No box ever says nothing.** Every widget and every object resolves to one
  of three statements, visually distinct: drawn; carried but of a type this
  page does not lay out, named; or carrying nothing, with what is missing said
  plainly. The third wins over the second, because "this widget carries no
  configuration at all" is a fact about the admin's content and "this preview
  does not lay out Skittles" is a fact about the tool. It covers objects too:
  a view with no columns, a super metric with an empty formula, an alert with
  no symptom sets, a group with no membership rules, a dashboard with no
  widgets. Every one of those branches carries its own code, one per branch
  rather than one shared by three, and the suite asserts the fixture
  exercises all of them, so a new one fails until something does. The notes
  count every box the page shows, grouped by reason rather than by sentence.

  Two things are deliberately *not* emptiness, because in both the widget
  works and the gap is elsewhere. A widget whose column layout lives in the
  saved state VCF Operations writes (`states[].value`, a nested encoded blob
  this page does not decode) is configured, and calling it unconfigured was
  false. A widget naming a view the export does not carry is pointing at
  content an export cannot hold: an export declares `type=CUSTOM` and carries
  custom content only, so a view that ships in a management pack is never in
  one, and nothing in an export tells that apart from a view that is
  genuinely gone. Both get their own state, their own count, and the wording
  `inspect` already uses for navigation links.

  Corpus figures come from `tools/corpus_census.py`, which states the identity
  rule it implements, is order independent, and reports where two copies of
  one identity disagree rather than letting whichever was read first win.

  **The preview shows the dashboard's wiring.** `widgetInteractions` is read
  and rendered: a summary above the grid naming which widget drives which, a
  badge and a coloured edge per provider and receiver, and on a receiver a
  line saying whose selection its mock values stand for. Values are shown
  rather than an empty frame, because the preview exists so a widget can be
  recognised and a blank box shows neither its columns nor its metrics; the
  label is what keeps that honest. `selfProvider` is read in both the nested
  and the flat spelling (every corpus widget nests it). How a widget comes by
  its subject has four answers and the preview gives the right one, deciding
  from the wiring before it looks at the configuration: fed by a named widget;
  a selector, which chooses no subject of its own precisely because it drives
  the widgets below it, whatever its own configuration looks like; driven from
  outside the dashboard, which is how a dashboard opened in an object's
  context works; or waiting on a selection nothing on the dashboard provides,
  which is the only one of the four that will never show data and the only one
  counted as state 3. Asking the configuration first is what let a widget
  carry a badge saying how many widgets it drives directly above a box saying
  it showed nothing.

  Widths and order are the dashboard's own, heights are the preview's (taken
  from the content, so nothing is clipped), and the banner on every page says
  so. A widget the dashboard places outside its own declared columns widens
  the grid rather than being squashed into a sliver; if anything still has to
  be clamped, the notes name the widget and what happened to it.

  Values are made up and derived by hash from the keys and names the export
  already carries, never from the clock or a random source, so the same
  export previews identically on every run and in every process. Magnitudes
  follow the unit the export declares. Names, titles, columns and keys are
  never invented. A text widget's markup is shown as text, never injected.
  One self-contained file: inline CSS, inline SVG, no script, no font, no
  image, no CDN.

- `ui`: the selection page, which is now the whole job without a command
  line. The dependency tree with a checkbox per object, grouped by kind and
  with each object's dependencies in a nested disclosure, so 66 dashboards
  and 430 objects stay readable. Checking something pulls in what it needs
  and says what it pulled in; anything pulled in is labelled with what
  requires it; unchecking something another selection still needs is refused
  and names what needs it, and nothing changes on a refusal. The preview of
  any object shows in place. The counts of what a build would carry sit above
  a Build button that writes the bundle and says where it went. Controls for
  every command (`inspect`, `tree`, `preview`, `build`, `corpus-check`), a
  text box that takes a selection the way `build --select` takes a file, and
  a button that hands back the equivalent command line.

  A bundle built through the page is byte for byte the bundle the command
  line writes for the same selection: the page calls the same closure and the
  same writer, and the page's state object holds every rule in one place so
  the two ways in cannot drift. Still 127.0.0.1 only, still a same-origin
  check on every POST (now on endpoints that write files and read paths, not
  only on settings), still no external resource of any kind, and it works
  with the keyboard alone and at a narrow window.

- Bundles are reproducible. Every zip entry the tool writes carries a fixed
  timestamp rather than the clock, and a fixed `create_system`: neither is
  content and no import reads either, but leaving the time as "now" meant two
  builds of one selection differed in bytes, which cost the one comparison
  that proves the page and the command line agree, and leaving
  `create_system` to the platform meant the Windows binary and the Linux
  binary disagreed on the same selection.

- `corpus-check` now previews every object in every zip as well as running
  inspect, tree and the select-all build, and its ok line says how many. The
  preview is the command an admin looks at most, and the only proof it
  survived a real export used to be a one-off script.

- `tree`: the dependency tree over the export's own documents, readable by
  default and `--json` for machines. Every reference is read from the parsed
  document, never with a regex over its text, because one reference class is
  written in several shapes and a regex matches one of them. The edges:

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

  Every reference is read from the parsed document by walking the field, so a
  value that is an object in one widget and a list in the next is read the
  same way, and a shape this tool has never seen is reported rather than
  resolving to nothing. An edge to something a bundle cannot carry is shown
  as missing, named and counted, saying which it is: not in the export at
  all, or in it but in a member never carried, as a custom group's policy is; a by-name value that names no object here, which is the normal case
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
