# VCF Content Migrator

[![Release](https://img.shields.io/github/v/release/sentania-labs/vcf-cf-migrator?sort=semver)](https://github.com/sentania-labs/vcf-cf-migrator/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Move selected custom content between VCF Operations instances. Open a content
export, preview what is in it, and choose what to keep. The tool adds the
available dependencies and writes a smaller ZIP for you to import on the target.

It runs on your workstation. The ZIP workflow needs no instance credentials or
connection to either Operations instance. Previews use sample values, not live
monitoring data.

> This README describes the current source. The latest packaged release is
> available below; its interface may differ until the next release.

## Download and open

Download the file for your operating system from the
[latest release](https://github.com/sentania-labs/vcf-cf-migrator/releases/latest).
Choose the archive for your computer and extract it:

| Your computer | Download |
| --- | --- |
| Mac with Apple silicon (M-series chip) | `vcfcf-migrator-macos-arm64-app.zip` |
| Mac with an Intel processor | `vcfcf-migrator-macos-x86_64-app.zip` |
| Linux x86-64 | `vcfcf-migrator-linux.tar.gz` |
| Windows x86-64 | `vcfcf-migrator-windows.zip` |

On a Mac, **Apple menu > About This Mac** shows the chip or processor. Extract
the archive, drag **VCF Content Migrator.app** to **Applications**, and open it.
The app opens its own window without a Terminal window. Mac app downloads are
signed and notarized, with the notarization ticket attached to the app.

On Windows, extract and double-click `vcfcf-migrator.exe`. On Linux, extract the
archive, open a terminal in the extracted folder, and run:

```sh
./vcfcf-migrator
```

If the executable permission was lost when extracting, run
`chmod +x vcfcf-migrator` first. Keep the terminal open while using the Linux
app. All three platforms open a native window; the application does not start
a local web server.

Linux requires a graphical desktop and Qt's system libraries. The
[Ubuntu 24.04 package list](packaging/linux-runtime.txt) names the required
runtime packages; other distributions use different package names. The
command-line tools also work without a graphical session.

If your computer blocks the download, follow your organization's software
approval process. Include the release version and the exact message when
reporting a startup problem.

When the app opens, it checks GitHub for a newer stable release in the
background. If one is available, a small link beside the current version opens
the release page in your default browser. You choose whether to download and
install it. If you are offline or already up to date, no notice appears and
you can keep working. The check sends no export content or Operations
credentials. Command-line commands do not check for updates.

## Create a migration bundle

1. **Export from the source.** In VCF Operations, go to **Administration >
   Content > Export**, choose the content to export, and save the ZIP. Include
   the dependencies of the objects you intend to move. If Operations asks for
   an encryption password, keep it for the target import. The migrator does not
   need that password.
2. **Open the ZIP.** Use **Browse** in the desktop window, or enter the file's
   local path and choose **Open export**.
3. **Review and select.** Choose a content type and search by name. Click an
   object to see its preview, dependencies, and details. **Select shown** adds
   the filtered results; **Select all** includes the whole readable inventory.
   Required objects are added automatically. Removing a direct pick keeps it
   included if another pick still needs it.
4. **Review the bundle.** Choose **Review bundle** to check your picks and the
   dependencies they bring along. Resolve missing dependencies by using a more
   complete source export or removing the affected picks. Required references
   that cannot be identified as built-in content are treated as missing;
   there is no override. Policy references do not block a build, but their
   assignments must be checked on the target.
5. **Write and import.** Save to a new ZIP, then use the target instance's
   content import. Supply the original export password if requested. Open the
   imported objects on the target and check that their widgets, data sources,
   and links work.

![Dashboard preview with sample values](docs/preview-dashboard.png)

The preview helps you recognize content before moving it. It preserves the
export's names, layout, columns, and metric keys, but its values are invented.
It cannot confirm that the target has the adapters or resources a dashboard
needs.

## Connect directly to Operations

Instead of downloading an export yourself, expand **Open export > Connect to
Operations**. Enter the HTTPS address, username, password, and an export
encryption password. Add the authority source if your account requires one.
For an internal certificate authority, provide the path to its trusted PEM CA
file. Certificate verification is always enabled.

Choose **Connect and load**. Operations exports the supported custom content;
when the download finishes, select and build your bundle as usual. Keep the
export encryption password for the target import. It must meet the source
instance's password requirements.

Credentials are not saved. The downloaded export is held in a private temporary
folder until you replace it or close the app. Save your migration bundle before
closing. After an abnormal shutdown, temporary files may remain on the workstation.

Do not run another content export with the same account while connecting. The
Operations API serves the account's latest export. The migrator checks the job
identity before and after downloading, but cannot fully rule out a competing
export started at the same time. If a connection fails or times out, check the
source export job before trying again. Your existing inventory and selection
remain available when the replacement cannot be loaded.

## What carries across

Supported content includes dashboards, views, super metrics, custom groups,
symptoms, alert definitions, recommendations, reports, notification rules,
notification templates, and outbound settings.

Selected content documents are copied unchanged. Encrypted settings stay
encrypted, and the original export remains your source copy. Policies and other
unsupported export members are not included in the new bundle. Review warnings
before importing; this is a content selection tool, not a backup or a check of
the target's configuration.

Linked dashboards are included automatically, including their dependencies.
If a destination dashboard is missing from the export, bundle creation stops.
Include it in a new export or remove the dashboard that needs it from your
selection. Navigation links that name a missing widget also block the build;
repair those links in Operations and export again. This applies to built-in
dashboards too. After import, check the links on the target instance.

## If something goes wrong

- **Missing dependencies:** export the required content from the source, or
  remove the picks that depend on it. A target might already have an object,
  but the offline tool cannot verify that.
- **Unexpected inventory count:** do not assume a manifest total equals the
  selectable inventory. One reported dashboard-count discrepancy remains
  unresolved in [#37](https://github.com/sentania-labs/vcf-cf-migrator/issues/37).
- **Empty widgets after import:** allow Operations time to finish processing,
  then check the target's adapters, resources, and widget configuration.
- **Slow loading or selection:** let the current action finish before trying
  again. Include the export size and the operation you were performing in a
  problem report.

In **Help & diagnostics**, choose **Save anonymized diagnostics** and attach
that file to an [issue](https://github.com/sentania-labs/vcf-cf-migrator/issues).
Include the application version, what you tried, and what happened.

Shared diagnostics retain counts, timings, and operation results while
replacing paths, account details, machine details, and content identifiers with
anonymous labels. **Local file logs are private, not anonymized diagnostics.**
Do not attach them, source exports, or screenshots containing private content
to a public issue. Previously saved reports are not changed by an upgrade.

## Command-line use

Mac command-line users can download `vcfcf-migrator-macos-arm64.zip` or
`vcfcf-migrator-macos-x86_64.zip`. These separate archives each contain the
standalone `vcfcf-migrator` executable. They do not require the `.app` to be
installed. A bare Mac executable cannot carry an attached notarization ticket,
so its first launch may need to contact Apple. Older releases provide
executables directly, with the platform in the filename.

The same tools are available from a terminal. These examples assume the
executable is named `vcfcf-migrator` and is on your PATH; otherwise use its full
path or downloaded filename.

```sh
vcfcf-migrator ui source-export.zip
vcfcf-migrator inspect source-export.zip
vcfcf-migrator tree source-export.zip
vcfcf-migrator preview source-export.zip 'Dashboard name' --out preview.html
vcfcf-migrator build source-export.zip --select selection.txt --out migration.zip
```

In `selection.txt`, put one object name or identifier on each line. Blank lines
and lines starting with `#` are ignored. Use an identifier when names are not
unique. `vcfcf-migrator build --help` lists the build options.

For Python users, the release also includes a wheel. Install it with
`python -m pip install /path/to/the-downloaded.whl` using Python 3.9 or newer.
Installation downloads dependencies; working with local exports afterward
needs no instance connection.

Built on the [VCF Content Factory](https://github.com/sentania-labs/vcf-content-factory)
core library. Development and design notes are in [docs](docs/README.md).
