# docs

`design/` is why this tool is the way it is: the pass-through contract, the
evidence from importing bundles into a live instance, the reasoning behind
dropping version handling, and the release log. `reviews/` holds the review
records that produced it.

Both lived in the VCF Content Factory until v0.3.0, because that is where this
tool was built. They are here now, so a change to this repo can be judged
against the reasons for it without leaving the repo.

What stayed behind is the library's own record. `vcf-cf-tooling-core` is
maintained in the factory, and `tooling-core-carveout-v1.md` plus the
`m2-row*` reviews there explain how it was carved out and where its boundary
is. This repo is a consumer of that library, not its owner.

## Depending on vcfcf_core

`pyproject.toml` pins a wheel built by a `core-v*` release of the factory, by
URL. That is deliberate and not a step on the way to publishing on PyPI.

A pinned wheel URL means a build of this tool is reproducible from two git
tags and nothing else: no package index, no account, no risk of a version
being yanked from under it, and it still works on a machine with no route to
the internet beyond GitHub. Scott, verbatim: "each build of the thing can be
fine to always be pinned against a certain set of version of the VCF Content
Factory tooling."

So when this repo needs something from the library, the path is: open an issue
on `sentania-labs/vcf-content-factory`, the change lands and is released as a
new `core-vX.Y.Z`, and the pin here moves in its own PR. The library is not
changed from this side.

Every fix in v0.3.0 was made here without touching the library, which is the
evidence that this arrangement costs little.

## Checking that nothing here came from the corpus

    python3 tools/corpus_leak_scan.py docs README.md

It harvests every uuid from every member of every corpus zip, nested zips
included, and fails if one of them appears in the files given. Run it before
committing anything that quotes an export.

Four of the documents in `reviews/` and `design/` discuss values that were
copied out of the corpus while describing a leak. Those are redacted here as
`<corpus-uuid, redacted>`: the reasoning is what the records are for, and the
literal identifier is not. The unredacted originals remain in the factory
repo, which is a separate decision and Scott's to make.

This scan is deliberately **not** in CI. CI has no corpus, so it would report
"nothing scanned" and pass forever, which is worse than no gate: it looks like
one. It is a local check, and it reads text only, which is why the screenshot
rule below exists separately.

# The screenshots in this directory

Both images are rendered from `corpus/devel-9.0.2.0-2026-09-14-full.zip`, the
lab's own development instance, and they show that instance's dashboard names,
content uuids and the owner account uuid that the preview header prints.

That is authorized. Scott, verbatim, 2026-09-14: "As long as the references are
my devel, who cares." The same message ruled out the other exports, and that
rule stands: **screenshots come from the devel export only.** Not from the prod
export, and not from either of the exports carrying another admin's content,
because those carry other people's names and account ids and nobody has
authorized those.

One thing to know before adding an image here. The corpus leak scan reads text:
it walks every blob in every revision and matches needles generated from the
corpus. It cannot read a PNG. A screenshot taken from prod would therefore pass
the scan, be committed, and still put a real account uuid in a public repo as
pixels. The scan is not the control for images; this rule is.
