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
