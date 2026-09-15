# vcf-cf-migrator

[![Release](https://img.shields.io/github/v/release/sentania-labs/vcf-cf-migrator?sort=semver)](https://github.com/sentania-labs/vcf-cf-migrator/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

You have an older VCF Operations instance carrying years of custom content,
and you are standing up a new one. You do not want to drag all of it across.
But a content export is all or nothing, the names alone will not tell you what
a dashboard is, and pulling one out by hand breaks the views and super metrics
under it.

Review it, preview it, and write a new import bundle holding only what you
picked and everything it needs.

![A dashboard from an export, laid out widget by widget before you decide to carry it](docs/preview-dashboard.png)

Every object in the export gets a page like this one. The values are invented;
the names, columns, metric keys, layout and wiring are the export's own.

## Run it

Download the binary for your machine from the
[latest release](https://github.com/sentania-labs/vcf-cf-migrator/releases/latest),
then:

```
vcfcf-migrator ui my-export.zip
```

That opens a window, which does the whole job. Nothing listens on a port and
nothing leaves your machine.

On Linux, or anywhere without a system webview, the same page opens in your
browser instead, served on 127.0.0.1. `--server` asks for that deliberately.

First run: `chmod +x` the file on Linux and macOS. On Windows, Defender may
refuse to start a freshly published binary simply because it has not seen it
before; if that happens, install with pip instead, using the `.whl` from the
same release page.

![Tick a dashboard and the views and super metrics under it come with it](docs/selection-and-dependencies.png)

Tick what you want. What it depends on comes with it, said out loud, and the
tool will not let you drop something the rest of your selection still needs.
Then Build the bundle, and import it on the target the way you exported.

An imported dashboard finishes arriving in the background, which can take
minutes or hours, and while it does its widgets may look empty. That is normal
and it completes on its own; opening the dashboard once is the quickest way to
move it along. If one stays that way, it is usually because the target does not
have an adapter the dashboard's widgets need.

It handles dashboards, views, super metrics, groups, symptoms, alerts, reports
and notification rules, runs on your workstation, and never talks to either
instance, so it needs no credentials and no network. From the
[VCF Content Factory](https://github.com/sentania-labs/vcf-content-factory), on
that project's
[`vcf-cf-tooling-core`](https://github.com/sentania-labs/vcf-content-factory/releases?q=core-v).

## Get an export

In VCF Operations on the source instance: Administration, then Content, then
Export. Choose the content you want out, or all of it, and save the zip. If it
includes notification rules or outbound settings, VCF Operations asks for an
encryption password. Remember it: this tool never needs it, but whoever imports
the bundle on the target does.

## What it does with your content

It carries documents through untouched. A dashboard you select arrives byte for
byte as the export held it, and the tool rebuilds the containers around them and
nothing else, so anything it does not understand survives the trip. The same
jobs are on the command line if you want to script them: `inspect`, `tree`,
`preview <object>`, `build --select <file> --out <bundle.zip>`. `--help` has the
detail.

## Reporting a problem

`--log run.log` records what it did and why, in enough detail to diagnose a
failure without the export. Logs carry content names and ids, and the file
paths you gave the tool, which on your machine may carry your own user name.
They never carry credentials, the export password or encrypted values, and a
dashboard's owner appears as `owner-1`. People are excluded with two stated
limits: a name of three characters or fewer, and a name that is also an
ordinary word such as `operator` or `support`, are left as they are, because
replacing those would rewrite your own content.
