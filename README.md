# vcf-cf-migrator

Move custom content between VCF Operations instances: dashboards,
views, super metrics, groups, symptoms, alerts, reports and
notification rules. Point it at a content export, see what depends on
what, preview anything before you take it, pick what you want, get an
import bundle.

Runs on your workstation. It never talks to either instance, so it
needs no credentials and no network.

From the [VCF Content Factory](https://github.com/sentania-labs/vcf-content-factory),
and built on that project's
[`vcf-cf-tooling-core`](https://github.com/sentania-labs/vcf-content-factory/releases?q=core-v)
library.

## Get an export

In VCF Operations on the source instance: Administration, then Content,
then Export. Choose the content you want out, or all of it, and save the
zip. That zip is what this tool reads.

If the export includes notification rules or outbound settings, it asks
for an encryption password. Remember it. This tool never needs it, but
whoever imports the bundle on the target does.

## Run it

Download the binary for your machine from the
[latest release](https://github.com/sentania-labs/vcf-cf-migrator/releases/latest),
then:

```
vcfcf-migrator ui my-export.zip --source-version 9.0.2
```

That opens the page, which does the whole job. An export does not record
which version it came from, so tell it; 8.10 and later are accepted.

First run: `chmod +x` the download on Linux and macOS. macOS blocks
unsigned binaries once, so allow it under System Settings, Privacy and
Security. Windows SmartScreen wants More info, then Run anyway.

Then import the bundle on the target the same way you exported.

## Command line

The page is the tool. These are the same jobs if you want to script
them: `inspect` lists what is in an export, `tree` shows what depends on
what, `preview <object>` writes an HTML page for one object, and `build
--select <file> --out <bundle.zip>` writes the bundle. `--help` has the
detail.

## What it does with your content

It carries documents through untouched. A dashboard you select arrives
byte for byte as the export held it, with the views and super metrics it
needs alongside. The tool rebuilds the containers around them and
nothing else, so anything it does not understand still survives the
trip. It will not let you take a dashboard without its views.

## Reporting a problem

`--log run.log` records what it did and why, in enough detail to
diagnose a failure without the export. Logs carry content names and ids.
They never carry credentials, the export password, encrypted values, or
anything about people.
