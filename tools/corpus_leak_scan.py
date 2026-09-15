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
TEXT_SUFFIXES = {".md", ".py", ".txt", ".json", ".yml", ".yaml", ".toml", ".cfg", ".html"}


def harvest(corpus: pathlib.Path) -> set:
    """Every uuid in every corpus export, nested zips included."""
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

    for export in sorted(corpus.glob("*.zip")):
        try:
            with zipfile.ZipFile(export) as zf:
                walk(zf)
        except Exception as exc:  # pragma: no cover - a broken zip is not a leak
            print(f"warning: could not read {export}: {exc}", file=sys.stderr)
    return found


def scan(paths, needles: set):
    hits = []
    for root in paths:
        root = pathlib.Path(root)
        files = [root] if root.is_file() else sorted(root.rglob("*"))
        for one in files:
            if not one.is_file() or one.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = one.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for value in {m.lower() for m in UUID.findall(text)} & needles:
                hits.append((one.as_posix(), value))
    return hits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="files or directories to scan")
    parser.add_argument("--corpus", default="corpus", help="corpus directory (default: ./corpus)")
    args = parser.parse_args(argv)

    corpus = pathlib.Path(args.corpus)
    if not corpus.is_dir() or not list(corpus.glob("*.zip")):
        # Not a failure. CI has no corpus and must not pretend to have scanned.
        print(f"no corpus at {corpus}; nothing scanned")
        return 0

    needles = harvest(corpus)
    print(f"harvested {len(needles)} uuids from {len(list(corpus.glob('*.zip')))} exports")
    hits = scan(args.paths, needles)
    if not hits:
        print("clean: nothing in those paths came from the corpus")
        return 0
    print(f"\n{len(hits)} corpus value(s) found:")
    for where, value in sorted(hits):
        print(f"  {where}: {value}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
