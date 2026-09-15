#!/usr/bin/env python3
"""Does any text in this repo come from the corpus?

The corpus is five real VCF Operations exports from the lab. They are
gitignored and must stay that way, but a value copied out of one and pasted
into a document is not covered by a gitignore: on 2026-09-14 an account uuid
reached the public repo that way and took a force-push, a relaxed branch
protection and a deleted release to remove.

The scan that found it was written on the spot and thrown away, so when six
more corpus values turned up in review records a day later, nothing had been
watching. This is that scan, kept.

It harvests every uuid from every member of every corpus zip, including the
nested zips, and reports any that also appear in the files given. It reads
text only: a screenshot cannot be scanned, which is why docs/README.md carries
a separate rule for images.

    python3 tools/corpus_leak_scan.py docs README.md
    python3 tools/corpus_leak_scan.py --corpus /path/to/corpus docs

Exit 1 if anything is found, so it can gate.
"""
from __future__ import annotations

import argparse
import io
import pathlib
import re
import sys
import zipfile

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
UUID_BYTES = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# A uuid is not the whole class. The design record is explicit that the guard
# is "every uuid and every name", and records that the first hand-written
# version of this check missed a real display name. So names are harvested too,
# from the attributes and keys an export uses to carry one.
NAME_BYTES = re.compile(
    rb"(?:name|displayName|display_name|title|owner|ownerName|userName|fullName)"
    rb"""\s*[=:]\s*["']([^"'<>]{1,200})["']""", re.I)

# Below this length, and without a space, a name is not evidence. Harvesting
# every value under a name= attribute pulls in the export format's own
# vocabulary: attributeKey, resourceKind, advancedTimeMode. Those are schema
# terms that appear in this repo's prose because the repo documents the
# format, and a needle that matches its own documentation fails for the wrong
# reason. Of 81 names harvested from the corpus, 45 are vocabulary with no
# space in them and 36 are real display names. The same trap, reached from the
# other direction, is written up in tests/test_log_redaction_corpus.py.
MIN_NAME = 12

# Strings a person looked at and accepted. A gate with no way to record a
# decision gets switched off the first time it is wrong; this is the way to
# say "checked, and it is fine" without weakening it for everything else.
ALLOW_FILE = "tools/corpus_leak_allow.txt"

# .xml matters most of all: a content export is mostly XML, and the records
# being scanned quote it. Leaving it out was the allowlist quietly excluding
# the format the corpus is actually written in.
TEXT_SUFFIXES = {".md", ".py", ".txt", ".json", ".yml", ".yaml", ".toml", ".cfg",
                 ".html", ".xml", ".csv", ".ini", ".sh", ".rst"}


def harvest(corpus: pathlib.Path) -> set:
    """Every uuid and every long-enough name in every export, nested zips
    included. Raises OSError if an export cannot be read: a scan that skipped
    an archive has not checked the values only that archive held, and must not
    report clean."""
    found = set()

    def walk(zf: zipfile.ZipFile) -> None:
        for name in zf.namelist():
            try:
                data = zf.read(name)
            except Exception:
                continue
            if name.endswith(".zip"):
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as inner:
                        walk(inner)
                except Exception:
                    pass
            for match in UUID_BYTES.findall(data):
                found.add(match.decode("ascii").lower())
            for match in NAME_BYTES.findall(data):
                try:
                    name = match.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                if len(name) >= MIN_NAME and " " in name:
                    found.add(name.lower())

    for export in sorted(corpus.glob("*.zip")):
        try:
            with zipfile.ZipFile(export) as zf:
                walk(zf)
        except Exception as exc:
            # Not a warning. If this archive holds a value no other one does,
            # skipping it and printing "clean" certifies a scan that never
            # happened.
            raise OSError(f"could not read {export}: {exc}") from exc
    return found


def _allowed() -> set:
    """Strings a person has looked at and accepted, with a reason on file."""
    path = pathlib.Path(ALLOW_FILE)
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            out.add(line)
    return out


def scan(paths, needles: set):
    """Scan the working tree. A path named explicitly is always read, whatever
    its suffix: the allowlist is there to skip binaries while walking a
    directory, not to overrule someone who asked for a particular file."""
    hits = []
    for root in paths:
        root = pathlib.Path(root)
        if root.is_file():
            files, explicit = [root], True
        else:
            files, explicit = sorted(root.rglob("*")), False
        for one in files:
            if not one.is_file():
                continue
            # The allowlist quotes the values it accepts, so scanning it finds
            # every one of them and says nothing useful.
            if one.as_posix().endswith(ALLOW_FILE):
                continue
            if not explicit and one.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = one.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for value in _values_in(text) & needles:
                hits.append((one.as_posix(), value))
    return hits


def _values_in(text: str) -> set:
    """Everything in this text that could be a harvested value."""
    found = {m.lower() for m in UUID.findall(text)}
    lowered = text.lower()
    # Names are matched as substrings rather than re-extracted, because a name
    # copied into prose no longer sits beside the key it came from.
    for needle in _NAME_NEEDLES:
        if needle in lowered:
            found.add(needle)
    return found


_NAME_NEEDLES: set = set()


def scan_history(needles: set, limit: int = 0):
    """Every blob in every revision, which is where the first one of these was
    found. A value committed and later deleted is still publicly reachable, so
    a scan of the working tree alone can report clean on an exposure that is
    very much live."""
    import subprocess

    listing = subprocess.run(["git", "rev-list", "--objects", "--all"],
                             capture_output=True, text=True, check=True).stdout
    blobs = []
    for line in listing.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            blobs.append((sha, path))
    if limit:
        blobs = blobs[:limit]
    hits = []
    batch = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE)
    try:
        for sha, path in blobs:
            if pathlib.Path(path).suffix.lower() not in TEXT_SUFFIXES:
                continue
            batch.stdin.write((sha + "\n").encode())
            batch.stdin.flush()
            header = batch.stdout.readline().decode().split()
            if len(header) < 3:
                continue
            size = int(header[2])
            data = batch.stdout.read(size)
            batch.stdout.read(1)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for value in _values_in(text) & needles:
                hits.append((f"{path} (blob {sha[:8]})", value))
    finally:
        batch.stdin.close()
        batch.wait()
    return hits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["."],
                        help="files or directories to scan (default: the whole repo)")
    parser.add_argument("--corpus", default="corpus", help="corpus directory (default: ./corpus)")
    parser.add_argument("--history", action="store_true",
                        help="also scan every blob in every revision")
    args = parser.parse_args(argv)
    paths = args.paths or ["."]

    corpus = pathlib.Path(args.corpus)
    if not corpus.is_dir() or not list(corpus.glob("*.zip")):
        # Not a failure. CI has no corpus and must not pretend to have scanned.
        print(f"no corpus at {corpus}; nothing scanned")
        return 0

    try:
        needles = harvest(corpus)
    except OSError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        print("refusing to report clean on an incomplete harvest", file=sys.stderr)
        return 2
    allow = _allowed()
    if allow:
        needles -= allow
        print(f"{len(allow)} value(s) accepted by {ALLOW_FILE}")
    global _NAME_NEEDLES
    _NAME_NEEDLES = {n for n in needles if not UUID.fullmatch(n)}
    print(f"harvested {len(needles)} values "
          f"({len(needles) - len(_NAME_NEEDLES)} uuids, {len(_NAME_NEEDLES)} names) "
          f"from {len(list(corpus.glob('*.zip')))} exports")

    hits = scan(paths, needles)
    if args.history:
        print("scanning every blob in every revision")
        hits += scan_history(needles)
    if not hits:
        where = "those paths and the whole history" if args.history else "those paths"
        print(f"clean: nothing in {where} came from the corpus")
        return 0
    print(f"\n{len(hits)} corpus value(s) found:")
    for where, value in sorted(set(hits)):
        shown = value if UUID.fullmatch(value) else value[:40] + "..."
        print(f"  {where}: {shown}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
