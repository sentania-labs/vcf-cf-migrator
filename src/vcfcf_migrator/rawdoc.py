"""Byte-exact document slicing, the floor the pass-through contract stands on.

The spec's one absolute rule (``content-migrator-v1.md``, "Pass-through is
the contract"): a selected object's document goes into the output bundle as
the bytes the export carried, never as a re-serialization of a parsed model.
Copying bytes cannot lose a field the tool has never seen; re-rendering a
parsed model silently can.

An export does not store one object per file, so carrying bytes means
carrying a *slice*: the ``ViewDef`` element inside ``views.zip/content.xml``,
the value under one uuid key in ``supermetrics.json``, one entry of the
``dashboards`` array inside an owner's inner zip. This module finds those
slices and hands back their exact byte (XML) or character (JSON) ranges, plus
the container scaffolding needed to write a fresh container around a subset
of them.

Two readers, deliberately low level:

* ``xml_container`` walks a document with ``expat`` and records the byte range
  of every element with a wanted tag, together with the raw start tags of its
  ancestors and the prologue before the root. Nothing is re-serialized: the
  rebuilt container is the original prologue and start tags, the copied
  element bytes, and matching close tags.
* ``json_members`` / ``json_items`` walk an object or array with
  ``json.JSONDecoder.raw_decode`` and record the character range of every
  value. A rebuilt object is assembled from re-encoded *keys* (identity, not
  content) and copied *values*.

Slicing on ``str`` for JSON and on ``bytes`` for XML is not an inconsistency:
a JSON document is decoded once as UTF-8 and every slice re-encodes to the
same bytes it came from, while an XML document is never decoded at all.

Two limits of the XML side, both deliberate and both narrow:

* **Encoding.** Slicing is encoding-blind, but *rebuilding* is not: the
  separators and close tags a rebuilt container needs are only safe in an
  ASCII-compatible encoding. Such a document is refused outright rather than
  rebuilt into something subtly wrong, and the encoding is read from the
  *bytes* (BOM, or a NUL among the opening four) rather than from the
  declaration, because a real UTF-16 document's declaration is not readable
  until you already know the encoding. Close tags are cut from the source's
  own start-tag bytes, so a tag name outside ASCII survives whatever
  ASCII-compatible encoding carried it. Every content XML in all five corpus
  exports is UTF-8.
* **Namespace prefixes.** The parser runs without namespace processing, so a
  prefixed tag reads as ``n:ViewDef`` and will not match a wanted tag of
  ``ViewDef``. This fails safe: the member is listed as carried and not
  inspected rather than carried empty. No corpus export uses a prefix.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple
from xml.parsers import expat


class RawDocError(Exception):
    """A container that could not be read as the format it claims to be."""


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawElement:
    """One element of a wanted tag, as the bytes the export holds."""
    tag: str
    attrib: dict
    start: int
    end: int
    path: Tuple[str, ...]  # ancestor tags, root first

    def raw(self, data: bytes) -> bytes:
        return data[self.start:self.end]


@dataclass
class XmlContainer:
    """Everything needed to write a fresh container around copied elements."""
    prologue: bytes                 # xml declaration and anything before the root
    open_tags: List[bytes]          # raw start tags, root first, down to the parent
    close_tags: List[bytes]         # matching close tags, innermost first
    elements: List[RawElement]

    def rebuild(self, elements: Sequence[RawElement], data: bytes) -> bytes:
        """The container with only *elements* in it, each copied verbatim."""
        if not self.open_tags:
            # Nothing of the wanted tag was ever found, so there is no
            # container shape to write. Callers never rebuild one of these;
            # returning empty is what keeps a malformed fragment impossible.
            return b""
        parts = [self.prologue]
        for tag in self.open_tags:
            parts.append(tag)
            parts.append(b"\n")
        for el in elements:
            parts.append(el.raw(data))
            parts.append(b"\n")
        for tag in self.close_tags:
            parts.append(tag)
            parts.append(b"\n")
        return b"".join(parts)


# Encodings expat can parse *and* whose bytes for ``<``, ``/``, ``>`` and a
# newline are the ASCII ones, which is what a rebuilt container's scaffolding
# assumes. Deliberately narrow: Shift_JIS, EUC and KOI8 are ASCII-compatible on
# paper but expat refuses multi-byte encodings outright, so whitelisting them
# only converts a clean refusal into a crash further down.
_REBUILDABLE = ("utf-8", "utf8", "us-ascii", "ascii", "iso-8859", "latin",
                "windows-12", "cp12")

# Byte signatures of the encodings that are not ASCII-compatible. An XML
# document in one of them does not begin with the ASCII bytes ``<?xml``, which
# is exactly why reading the declaration is not enough to catch it: the
# declaration is unreadable until you already know the encoding.
_BOMS = (
    (b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\xfe\xff", "utf-16-be"), (b"\xff\xfe", "utf-16-le"),
)


def _declared_encoding(data: bytes) -> Optional[str]:
    """The ``encoding="..."`` of an XML declaration, lowercased."""
    head = data[:200]
    if not head.startswith(b"<?xml"):
        return None
    end = head.find(b"?>")
    if end < 0:
        return None
    match = re.search(rb'encoding\s*=\s*["\']([^"\']+)["\']', head[:end])
    return match.group(1).decode("ascii", "replace").lower() if match else None


def sniff_encoding(data: bytes) -> Optional[str]:
    """The document's encoding as the *bytes* give it, not as they claim.

    A BOM first, then the shape of the opening bytes: a UTF-16 or UTF-32
    document with no BOM still starts with a NUL somewhere in its first four
    bytes, because its first character is an ASCII ``<``. Only then the
    declaration, which is readable at all only once the encoding is
    ASCII-compatible. None when nothing says.
    """
    for bom, name in _BOMS:
        if data.startswith(bom):
            return name
    head = data[:4]
    if b"\x00" in head and not head.startswith(b"\x00\x00\x00\x00"):
        return "utf-16 or utf-32 (no BOM, NUL in the opening bytes)"
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8"
    return _declared_encoding(data)


def _refuse_unrebuildable_encoding(data: bytes) -> None:
    """Refuse a document this module could slice but could not rebuild.

    Slicing is encoding-blind; rebuilding is not, because the separators and
    close tags a fresh container needs are ASCII bytes. Refusing up front is
    the whole point: the alternative is a bundle that looks written and is not
    well-formed.
    """
    sniffed = sniff_encoding(data)
    if sniffed is None:
        return
    if not any(sniffed.startswith(ok) for ok in _REBUILDABLE):
        raise RawDocError(
            f"XML in encoding {sniffed!r} cannot be subset safely: rebuilding a "
            "container needs an ASCII-compatible encoding expat can parse, and "
            "guessing would corrupt it")


def _tag_name_bytes(start_tag: bytes) -> bytes:
    """The tag's name, as the bytes the document spells it with, so a close
    tag can be built without assuming the document is ASCII or UTF-8."""
    name = start_tag[1:]
    for i, ch in enumerate(name):
        if ch in b" \t\r\n/>":
            return name[:i]
    return name


def _end_of_tag(data: bytes, start: int) -> int:
    """Index just past the ``>`` that closes the tag beginning at *start*.

    Quote aware: XML permits a bare ``>`` inside an attribute value, so a
    plain ``data.index(b">")`` is wrong on real exports.
    """
    i = start
    quote = 0
    while i < len(data):
        ch = data[i]
        if quote:
            if ch == quote:
                quote = 0
        elif ch in (0x22, 0x27):  # " '
            quote = ch
        elif ch == 0x3E:  # >
            return i + 1
        i += 1
    raise RawDocError("unterminated tag in XML container")


def xml_container(data: bytes, wanted_tags: Sequence[str],
                  parent: Optional[str] = None,
                  where: Optional[Callable[[dict], bool]] = None) -> XmlContainer:
    """Find every element whose tag is in *wanted_tags* and the scaffolding
    around the first one, so a subset can be written back.

    Elements are returned in document order. Only elements sharing the path of
    the first match are collected, and *parent* pins that path further: an
    ``AlertDefinition`` names a ``Recommendation`` by ``ref`` inside its own
    ``State``, and an ``AlertDefinition`` also wraps those references in a
    ``<Recommendations>`` of its own. *where* pins it the rest of the way: a
    reference carries ``ref``, a definition carries ``key`` and its text.
    """
    _refuse_unrebuildable_encoding(data)
    wanted = set(wanted_tags)
    parser = expat.ParserCreate()
    stack: List[Tuple[str, int]] = []
    elements: List[RawElement] = []
    ancestors: Optional[List[Tuple[str, int]]] = None
    pending: List[Tuple[str, dict, int, Tuple[str, ...]]] = []
    root_start = [-1]

    def on_start(name: str, attrs: dict) -> None:
        nonlocal ancestors
        idx = parser.CurrentByteIndex
        if not stack:
            root_start[0] = idx
        stack.append((name, idx))
        qualifies = name in wanted
        if qualifies and parent is not None:
            qualifies = len(stack) > 1 and stack[-2][0] == parent
        if qualifies and where is not None:
            qualifies = where(dict(attrs))
        if qualifies:
            if ancestors is None:
                # The first wanted element in document order fixes the path,
                # so a nested same-tag element deeper in the tree (a
                # Recommendation named by ref inside an AlertDefinition) can
                # never be mistaken for a definition.
                ancestors = list(stack[:-1])
            pending.append((name, dict(attrs), idx, tuple(t for t, _ in stack[:-1])))

    def on_end(_name: str) -> None:
        idx = parser.CurrentByteIndex
        name, start = stack.pop()
        if pending and pending[-1][2] == start:
            _tag, attrs, _start, path = pending.pop()
            # Where the element ends depends on how it was written, and expat
            # will not say. A self-closing element ends at its own ``/>``; any
            # other ends at the ``>`` of its closing tag, which is where expat
            # points. Reading that from the source (rather than from what
            # happens to follow the element) is what keeps a self-closing
            # element that butts straight up against its parent's closing tag
            # from swallowing it.
            tag_end = _end_of_tag(data, start)
            end = tag_end if data[tag_end - 2:tag_end] == b"/>" else _end_of_tag(data, idx)
            if tuple(t for t, _ in (ancestors or [])) == path:
                elements.append(RawElement(name, attrs, start, end, path))

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as e:
        raise RawDocError(f"not well-formed XML: {e}") from e
    except ValueError as e:
        # expat raises a bare ValueError for an encoding it will not handle
        # ("multi-byte encodings are not supported"). A refusal, not a crash.
        raise RawDocError(f"XML this parser cannot read: {e}") from e

    if ancestors is None:
        # No wanted element anywhere, so there is no container path to record.
        # ``rebuild`` refuses to write anything in that state.
        ancestors = []
    elements.sort(key=lambda el: el.start)
    prologue = data[:root_start[0]] if root_start[0] > 0 else b""
    open_tags = [data[start:_end_of_tag(data, start)] for _tag, start in ancestors]
    close_tags = [b"</" + _tag_name_bytes(tag) + b">" for tag in reversed(open_tags)]
    return XmlContainer(prologue=prologue, open_tags=open_tags,
                        close_tags=close_tags, elements=elements)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawValue:
    """One value inside a JSON object or array, as the export wrote it."""
    key: Optional[str]   # None for an array item
    start: int
    end: int

    def raw(self, text: str) -> str:
        return text[self.start:self.end]


_WS = " \t\n\r"
_DECODER = json.JSONDecoder()


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _value_end(text: str, i: int) -> int:
    try:
        _value, end = _DECODER.raw_decode(text, i)
    except ValueError as e:
        raise RawDocError(f"not well-formed JSON at offset {i}: {e}") from e
    return end


def json_members(text: str, start: int = 0) -> List[RawValue]:
    """Every member of the JSON object beginning at *start*, in file order."""
    i = _skip_ws(text, start)
    if i >= len(text) or text[i] != "{":
        raise RawDocError(f"expected a JSON object at offset {start}")
    i += 1
    out: List[RawValue] = []
    while True:
        i = _skip_ws(text, i)
        if i >= len(text):
            raise RawDocError("unterminated JSON object")
        if text[i] == "}":
            return out
        if text[i] == ",":
            i += 1
            continue
        key, i = _DECODER.raw_decode(text, i)
        if not isinstance(key, str):
            raise RawDocError("a JSON object key that is not a string")
        i = _skip_ws(text, i)
        if i >= len(text) or text[i] != ":":
            raise RawDocError("a JSON object member with no colon")
        i = _skip_ws(text, i + 1)
        end = _value_end(text, i)
        out.append(RawValue(key=key, start=i, end=end))
        i = end


def json_items(text: str, start: int = 0) -> List[RawValue]:
    """Every item of the JSON array beginning at *start*, in file order."""
    i = _skip_ws(text, start)
    if i >= len(text) or text[i] != "[":
        raise RawDocError(f"expected a JSON array at offset {start}")
    i += 1
    out: List[RawValue] = []
    while True:
        i = _skip_ws(text, i)
        if i >= len(text):
            raise RawDocError("unterminated JSON array")
        if text[i] == "]":
            return out
        if text[i] == ",":
            i += 1
            continue
        end = _value_end(text, i)
        out.append(RawValue(key=None, start=i, end=end))
        i = end


def member(values: Sequence[RawValue], key: str) -> Optional[RawValue]:
    for v in values:
        if v.key == key:
            return v
    return None


def build_object(pairs: Sequence[Tuple[str, str]]) -> str:
    """A JSON object from ``(key, raw value text)`` pairs, values untouched."""
    return "{" + ",".join(f"{json.dumps(k)}:{v}" for k, v in pairs) + "}"


def build_array(items: Sequence[str]) -> str:
    """A JSON array from raw value texts, each untouched."""
    return "[" + ",".join(items) + "]"
