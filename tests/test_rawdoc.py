"""Unit tests for the slicer the whole pass-through contract stands on.

``rawdoc`` is the one module where a silent off-by-one does not fail loudly:
it produces a document that is still well-formed and still imports, just
missing or gaining a fragment. Everything here is hand-written XML and JSON,
no fixture and no corpus, so the shapes under test stay pinned even if the
export fixture is rewritten.
"""
from __future__ import annotations

import json

import pytest

from vcfcf_migrator import rawdoc
from vcfcf_migrator.rawdoc import RawDocError


def _doc(body: str, declaration: str = '<?xml version="1.0" encoding="UTF-8"?>') -> bytes:
    return f"{declaration}<Content><Views>{body}</Views></Content>".encode("utf-8")


def _raws(data: bytes, tag="V", **kw):
    container = rawdoc.xml_container(data, [tag], **kw)
    return [el.raw(data) for el in container.elements]


# ---------------------------------------------------------------------------
# XML: where an element starts and stops
# ---------------------------------------------------------------------------

def test_a_self_closing_element_stops_at_its_own_slash_bracket():
    """The bug this branch fixed. A self-closing element butting straight up
    against its parent's closing tag was swallowing that closing tag, because
    the end was read from what followed rather than from the element itself.
    The adjacency is what makes it bite, so it is what is pinned here."""
    data = _doc('<V id="1"/>')
    assert _raws(data) == [b'<V id="1"/>']
    # Same element with whitespace after it: the case that always worked.
    assert _raws(_doc('<V id="1"/>\n')) == [b'<V id="1"/>']


def test_an_element_with_a_closing_tag_includes_it():
    assert _raws(_doc('<V id="1"><T>x</T></V>')) == [b'<V id="1"><T>x</T></V>']


def test_an_empty_element_written_the_long_way_round_trips():
    assert _raws(_doc('<V id="1"></V>')) == [b'<V id="1"></V>']


def test_siblings_come_back_in_document_order():
    data = _doc('<V id="1"/><V id="2">two</V><V id="3"/>')
    assert _raws(data) == [b'<V id="1"/>', b'<V id="2">two</V>', b'<V id="3"/>']


def test_a_bare_bracket_inside_an_attribute_value_is_not_the_end_of_the_tag():
    """XML allows an unescaped ``>`` in an attribute value, so scanning for
    the next ``>`` is wrong on real exports."""
    data = _doc('<V id="1" filter="a > b" note="c/>d"/>')
    assert _raws(data) == [b'<V id="1" filter="a > b" note="c/>d"/>']


def test_cdata_holding_a_closing_tag_is_not_mistaken_for_one():
    data = _doc('<V id="1"><![CDATA[</V>]]></V><V id="2"/>')
    assert _raws(data) == [b'<V id="1"><![CDATA[</V>]]></V>', b'<V id="2"/>']


def test_comments_and_processing_instructions_inside_an_element_survive():
    data = _doc('<V id="1"><!-- keep me --><?php nothing ?><T>x</T></V>')
    assert _raws(data) == [b'<V id="1"><!-- keep me --><?php nothing ?><T>x</T></V>']


def test_entities_are_copied_as_written_not_as_resolved():
    data = _doc('<V id="1" t="a &amp; b">&lt;not a tag&gt;</V>')
    assert _raws(data) == [b'<V id="1" t="a &amp; b">&lt;not a tag&gt;</V>']


def test_multibyte_text_before_an_element_does_not_shift_its_offsets():
    """expat reports byte offsets, not character offsets; a document with
    multibyte text ahead of the element is where that distinction shows."""
    data = _doc('<W>ééé ダッシュ</W><V id="1"/>')
    assert _raws(data) == [b'<V id="1"/>']


def test_a_nested_element_of_the_same_tag_is_not_a_second_definition():
    data = _doc('<V id="outer"><V id="inner"/></V>')
    assert _raws(data) == [b'<V id="outer"><V id="inner"/></V>']


def test_a_parent_filter_pins_which_wrapper_counts():
    """An AlertDefinition wraps its references in a <Recommendations> of its
    own, which is why the real caller passes both a parent and a predicate."""
    data = (b'<?xml version="1.0"?><alertContent>'
            b'<AlertDefinitions><AlertDefinition id="a"><State>'
            b'<Recommendations><Recommendation ref="r1"/></Recommendations>'
            b'</State></AlertDefinition></AlertDefinitions>'
            b'<Recommendations><Recommendation key="r1"><D>text</D></Recommendation></Recommendations>'
            b'</alertContent>')
    got = rawdoc.xml_container(data, ["Recommendation"], parent="Recommendations",
                               where=lambda attrib: not attrib.get("ref"))
    assert [el.raw(data) for el in got.elements] == [
        b'<Recommendation key="r1"><D>text</D></Recommendation>']


# ---------------------------------------------------------------------------
# XML: rebuilding the container
# ---------------------------------------------------------------------------

def test_rebuild_keeps_the_prologue_and_the_ancestor_tags_verbatim():
    data = _doc('<V id="1"/><V id="2"/>')
    container = rawdoc.xml_container(data, ["V"])
    out = container.rebuild(container.elements[:1], data)
    assert out.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert b"<Content>" in out and b"<Views>" in out
    assert b'<V id="1"/>' in out and b'<V id="2"/>' not in out
    # And what comes out can be read back to the same bytes.
    assert _raws(out) == [b'<V id="1"/>']


def test_rebuild_writes_nothing_when_the_document_held_no_wanted_element():
    data = _doc("<W/>")
    container = rawdoc.xml_container(data, ["V"])
    assert container.elements == []
    assert container.rebuild([], data) == b""


def test_a_close_tag_is_cut_from_the_source_not_assembled_in_ascii():
    """A tag name outside ASCII, in a non-UTF-8 but ASCII-compatible
    encoding: the close tag has to carry the source's own bytes."""
    text = ('<?xml version="1.0" encoding="iso-8859-1"?>'
            '<Cañón><V id="1"/></Cañón>')
    data = text.encode("iso-8859-1")
    container = rawdoc.xml_container(data, ["V"])
    out = container.rebuild(container.elements, data)
    assert out.decode("iso-8859-1").startswith('<?xml version="1.0" encoding="iso-8859-1"?>')
    assert "</Cañón>" in out.decode("iso-8859-1")


@pytest.mark.parametrize("data,why", [
    ('<?xml version="1.0" encoding="UTF-16"?><C><V id="1"/></C>'.encode("utf-16"),
     "real UTF-16 with a BOM"),
    ('<?xml version="1.0"?><C><V id="1"/></C>'.encode("utf-16-be"),
     "real UTF-16 with no BOM at all"),
    ('<?xml version="1.0"?><C><V id="1"/></C>'.encode("utf-32"),
     "real UTF-32"),
    ('<?xml version="1.0" encoding="Shift_JIS"?><C><V id="1"/></C>'.encode("ascii"),
     "an encoding expat refuses outright"),
    ('<?xml version="1.0" encoding="UTF-16"?><C><V id="1"/></C>'.encode("utf-8"),
     "ASCII bytes claiming to be UTF-16"),
])
def test_an_encoding_that_cannot_be_rebuilt_safely_is_refused(data, why):
    """The encoding is read from the bytes, not from the declaration: a real
    UTF-16 document's declaration is not readable until you already know the
    encoding, so a check that needs an ASCII ``<?xml`` at byte 0 fires only on
    the one case that was never dangerous."""
    with pytest.raises(RawDocError) as e:
        rawdoc.xml_container(data, ["V"])
    assert "cannot be subset safely" in str(e.value), why


@pytest.mark.parametrize("data", [
    b'<?xml version="1.0" encoding="UTF-8"?><C><V id="1"/></C>',
    '<?xml version="1.0" encoding="UTF-8"?><C><V id="1"/></C>'.encode("utf-8-sig"),
    b'<C><V id="1"/></C>',
    '<?xml version="1.0" encoding="iso-8859-1"?><C><V id="1"/></C>'.encode("iso-8859-1"),
])
def test_an_ascii_compatible_document_is_not_refused(data):
    assert len(rawdoc.xml_container(data, ["V"]).elements) == 1


def test_a_refused_encoding_never_reaches_the_parser_as_a_crash():
    """expat raises a bare ValueError for a multi-byte encoding. Whatever the
    route, the caller sees a RawDocError, never a traceback."""
    data = '<?xml version="1.0" encoding="euc-jp"?><C><V id="1"/></C>'.encode("ascii")
    with pytest.raises(RawDocError):
        rawdoc.xml_container(data, ["V"])


def test_malformed_xml_is_refused_not_guessed_at():
    with pytest.raises(RawDocError):
        rawdoc.xml_container(b"<C><V id='1'></C>", ["V"])


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def test_object_members_slice_each_value_exactly():
    text = '{"a": {"x": 1}, "b": [1, 2], "c": "s", "d": null}'
    got = {m.key: m.raw(text) for m in rawdoc.json_members(text)}
    assert got == {"a": '{"x": 1}', "b": "[1, 2]", "c": '"s"', "d": "null"}


def test_array_items_slice_each_item_exactly():
    text = '[ {"a": 1} , 2 , "three" ]'
    assert [i.raw(text) for i in rawdoc.json_items(text)] == ['{"a": 1}', "2", '"three"']


def test_a_value_is_copied_not_reformatted():
    """A float that would not survive a parse and re-dump unchanged."""
    text = '{"a": 1.10, "b": 1e3, "c": 0.30000000000000004}'
    raws = {m.key: m.raw(text) for m in rawdoc.json_members(text)}
    assert raws == {"a": "1.10", "b": "1e3", "c": "0.30000000000000004"}
    rebuilt = rawdoc.build_object([(k, raws[k]) for k in ("a", "b", "c")])
    assert "1.10" in rebuilt and "1e3" in rebuilt


def test_escapes_and_multibyte_survive_a_round_trip_through_bytes():
    text = json.dumps({"a": "café \\ \" </V> ダ"}, ensure_ascii=False)
    value = rawdoc.json_members(text)[0]
    assert value.raw(text).encode("utf-8") == json.dumps(
        "café \\ \" </V> ダ", ensure_ascii=False).encode("utf-8")


def test_nested_members_are_found_from_an_offset():
    text = '{"outer": {"inner": [1, 2, 3]}}'
    outer = rawdoc.member(rawdoc.json_members(text), "outer")
    inner = rawdoc.member(rawdoc.json_members(text, outer.start), "inner")
    assert inner.raw(text) == "[1, 2, 3]"
    assert len(rawdoc.json_items(text, inner.start)) == 3


def test_build_object_and_array_leave_values_untouched():
    assert rawdoc.build_object([("k", '{"raw":  1}')]) == '{"k":{"raw":  1}}'
    assert rawdoc.build_array(['{"a": 1}', "2"]) == '[{"a": 1},2]'


@pytest.mark.parametrize("text", ['[1, 2]', 'null', '"a"'])
def test_asking_for_an_object_where_there_is_none_is_refused(text):
    with pytest.raises(RawDocError):
        rawdoc.json_members(text)


def test_malformed_json_is_refused_not_guessed_at():
    with pytest.raises(RawDocError):
        rawdoc.json_members('{"a": }')
