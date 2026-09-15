"""The census script: reproducible, order independent, and explicit about
copies of one identity that disagree.

Three rounds of review caught a census that could not be recomputed, that
moved when the zips were read in a different order, and that stated one
identity rule while implementing another. These tests are what stop a fourth.

Fixture only: the script reads a corpus directory, and the corpus never
enters the repo, so the fixtures stand in for one here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from make_export_fixture import build_export_zip

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import corpus_census  # noqa: E402


@pytest.fixture
def corpus(tmp_path):
    """Two exports: the fixture, and the same content with the reports member
    dropped. The same objects appear in both, which is the corpus shape the
    identity rule exists for."""
    directory = tmp_path / "corpus"
    directory.mkdir()
    (directory / "a.zip").write_bytes(build_export_zip())
    (directory / "b.zip").write_bytes(build_export_zip(without=["reports.zip"]))
    return directory


def test_the_census_counts_distinct_content_not_occurrences(corpus):
    report = corpus_census.walk(corpus, "9.0.2")
    from make_export_fixture import EXPECTED_ITEMS

    # Two exports of one instance's content, so every occurrence count is
    # larger than its distinct count.
    assert report["occurrences"]["objects"] > report["objects"]
    assert report["occurrences"]["widgets"] > report["widgets"]
    assert report["occurrences"]["dashboards"] > report["dashboards"]
    # Distinct objects are the fixture's own item set, less the one report
    # the second export drops, and a dashboard under two owners is one
    # dashboard rather than two listings.
    assert report["objects"] == len(EXPECTED_ITEMS)
    assert report["dashboards"] == sum(1 for kind, _n, _u in EXPECTED_ITEMS
                                       if kind == "dashboard")


def test_the_census_does_not_depend_on_the_order_of_the_zips(corpus):
    paths = sorted(corpus.iterdir())
    forward = corpus_census.walk(corpus, "9.0.2", paths)
    backward = corpus_census.walk(corpus, "9.0.2", list(reversed(paths)))
    forward.pop("occurrences"), backward.pop("occurrences")
    assert forward == backward


def test_the_census_reports_copies_of_one_identity_that_disagree(tmp_path):
    """One dashboard uuid can carry different widgets under two owners. The
    union is counted and the disagreement is reported, rather than one copy
    silently winning by being read first."""
    import io
    import json
    import zipfile

    directory = tmp_path / "corpus"
    directory.mkdir()
    (directory / "a.zip").write_bytes(build_export_zip())
    # The same export with one widget removed from one dashboard.
    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in src.namelist():
            data = src.read(name)
            if name.startswith("dashboards/"):
                inner = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(data)) as dash_zip:
                    with zipfile.ZipFile(inner, "w") as w:
                        for member in dash_zip.namelist():
                            body = dash_zip.read(member)
                            if member.endswith("dashboard.json"):
                                doc = json.loads(body)
                                doc["dashboards"][0]["widgets"] = \
                                    doc["dashboards"][0]["widgets"][:-1]
                                body = json.dumps(doc).encode()
                            w.writestr(member, body)
                data = inner.getvalue()
            z.writestr(name, data)
    (directory / "b.zip").write_bytes(out.getvalue())

    report = corpus_census.walk(directory, "9.0.2")
    assert report["divergent dashboards"] >= 1
    # The union is what is counted, so the fuller copy decides the total and
    # reading order cannot change it.
    paths = sorted(directory.iterdir())
    other = corpus_census.walk(directory, "9.0.2", list(reversed(paths)))
    assert other["widgets"] == report["widgets"]


def test_the_census_renders_without_naming_a_single_object(corpus):
    """Nothing it prints is content: the corpus is the admin's own data, and
    a census that printed names could not be pasted into a PR body."""
    text = corpus_census.render(corpus_census.walk(corpus, "9.0.2"))
    assert "[Fixture]" not in text
    assert "distinct content across the corpus" in text


def _two_copies_classifying_differently(tmp_path):
    """Two exports of one dashboard uuid where the same widget id is empty in
    both copies for *different* reasons: no metric in one, no configuration at
    all in the other.

    Two different reasons rather than empty-against-clean, because that is
    what tells exclusion apart from a tie-break: sorting picks one of the two
    codes whichever way it sorts, and excluding picks neither.
    """
    import io
    import json
    import zipfile

    directory = tmp_path / "corpus"
    directory.mkdir()

    def build(mutate):
        src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for name in src.namelist():
                data = src.read(name)
                if name.startswith("dashboards/"):
                    inner = io.BytesIO()
                    with zipfile.ZipFile(io.BytesIO(data)) as dash_zip:
                        with zipfile.ZipFile(inner, "w") as w:
                            for member in dash_zip.namelist():
                                body = dash_zip.read(member)
                                if member.endswith("dashboard.json"):
                                    doc = json.loads(body)
                                    for widget in doc["dashboards"][0]["widgets"]:
                                        if widget.get("title") == "[Fixture] CPU over time":
                                            mutate(widget)
                                    body = json.dumps(doc).encode()
                                w.writestr(member, body)
                    data = inner.getvalue()
                z.writestr(name, data)
        return out.getvalue()

    def no_metric(widget):
        widget["config"]["metric"] = {"mode": "resourceKind", "resourceKindMetrics": [],
                                      "resourceMetrics": []}

    def no_config(widget):
        widget["config"] = {}
        widget.pop("states", None)

    (directory / "a.zip").write_bytes(build(no_metric))
    (directory / "b.zip").write_bytes(build(no_config))
    return directory


def test_a_widget_its_copies_classify_differently_is_reported_not_picked(tmp_path):
    """No tie-break: an earlier version sorted, which silently picked clean
    for widgets and empty for objects. A classification the exports disagree
    about is reported and left out of the per-reason totals."""
    directory = _two_copies_classifying_differently(tmp_path)
    report = corpus_census.walk(directory, "9.0.2")
    assert report["divergent widgets"] >= 1
    clean = corpus_census.walk(directory, "9.0.2", [directory / "a.zip"])
    # The disagreed widget is counted in neither per-reason total, so the
    # divergence cannot inflate or deflate the headline.
    assert (sum(report["widgets carrying nothing by reason"].values())
            < sum(clean["widgets carrying nothing by reason"].values())
            + report["divergent widgets"])


def test_order_independence_covers_the_classification_not_only_the_membership(tmp_path):
    """The order gate used to compare only which widgets exist, so a
    last-copy-wins mutation, which is exactly the defect of an earlier round,
    passed it."""
    directory = _two_copies_classifying_differently(tmp_path)
    paths = sorted(directory.iterdir())
    forward = corpus_census.walk(directory, "9.0.2", paths)
    backward = corpus_census.walk(directory, "9.0.2", list(reversed(paths)))
    forward.pop("occurrences"), backward.pop("occurrences")
    assert forward == backward
    assert forward["widgets carrying nothing by reason"] == \
        backward["widgets carrying nothing by reason"]
    assert forward["widget subjects"] == backward["widget subjects"]


def test_the_two_sides_of_the_union_rule_agree(tmp_path):
    """The widget total comes from the per-dashboard union and the
    classification map is built in the same walk; a mutation to either shows
    up as a disagreement here."""
    directory = _two_copies_classifying_differently(tmp_path)
    report = corpus_census.walk(directory, "9.0.2")
    assert report["widgets"] == report["widgets classified"]


def test_the_census_and_the_page_key_widgets_with_one_expression(corpus):
    """The page renders widgets tab by tab and the census walks them in
    document order, so "its position" meant two different positions: 16 of 66
    fixture widget occurrences mismatched and 14 distinct widgets were counted
    under another widget's verdict. Both sides now call
    ``preview.widget_keys``, and a miss raises rather than defaulting."""
    import json
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from vcfcf_migrator import graph as _graph
    from vcfcf_migrator import preview as _preview
    from vcfcf_migrator.export_reader import read_members

    members = read_members(corpus / "a.zip")
    graph = _graph.build_graph(members.data)
    for node in graph.by_kind("dashboard"):
        preview = _preview.build(graph, node)
        doc = json.loads(_preview.raw_document(graph, node))
        widgets = [w for w in doc.get("widgets", []) if isinstance(w, dict)]
        keys = _preview.widget_keys(widgets)
        # Every widget in the document has a verdict under the shared key,
        # and the fixture has id-less widgets spread across two tabs, which
        # is the shape that broke.
        assert set(keys.values()) == set(preview.widget_verdicts), node.key
        idless = [w for w in widgets if not w.get("id")]
        for widget in idless:
            # The key is the widget's index in the document, which is what the
            # census walks, and not its position within its tab.
            assert keys[id(widget)] == f"index-{widgets.index(widget)}"
        if node.name == "[Fixture] VM Overview":
            # The shape that broke: id-less widgets spread across two tabs,
            # so per-tab position and document index disagree.
            assert len(idless) >= 3
            assert len({w.get("tabId") for w in idless}) > 1


def test_a_missing_verdict_raises_rather_than_counting_as_something(corpus, monkeypatch):
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from vcfcf_migrator import preview as _preview

    # Key one widget differently from the page and the census must say so.
    original = _preview.widget_keys
    calls = {"n": 0}

    def drifted(widgets):
        calls["n"] += 1
        keys = original(widgets)
        if calls["n"] % 2 == 0 and keys:
            first = next(iter(keys))
            keys[first] = keys[first] + "-drifted"
        return keys

    monkeypatch.setattr(corpus_census._preview, "widget_keys", drifted)
    with pytest.raises(KeyError, match="keying widgets differently"):
        corpus_census.walk(corpus, "9.0.2")


def test_neither_divergence_tie_break_can_creep_back(tmp_path):
    """Sorting the disagreed classifications picks "clean" with ``[0]`` and
    "empty" with ``[-1]``, and the round-5 defect was the first of those.
    Because the two copies give the widget two different reasons, every
    tie-break lands on one of them and only exclusion lands on neither."""
    directory = _two_copies_classifying_differently(tmp_path)
    both = corpus_census.walk(directory, "9.0.2")
    only_a = corpus_census.walk(directory, "9.0.2", [directory / "a.zip"])
    only_b = corpus_census.walk(directory, "9.0.2", [directory / "b.zip"])
    a_reasons = only_a["widgets carrying nothing by reason"]
    b_reasons = only_b["widgets carrying nothing by reason"]
    reasons = both["widgets carrying nothing by reason"]
    # The divergent widget is one of each code in its own copy.
    assert a_reasons["widget-no-metric"] == reasons.get("widget-no-metric", 0) + 1
    assert b_reasons["widget-no-config"] == reasons.get("widget-no-config", 0) + 1
    assert both["divergent widgets"] >= 1


def test_an_object_its_copies_classify_differently_is_excluded_too(tmp_path):
    """The object side was ungated in both directions. The empty view is
    columnless in both copies and reported under a different code in each
    (a list view with no columns against a chart view with no attributes), so
    a tie-break in either direction lands on one of them and only exclusion
    lands on neither.
    """
    import io
    import zipfile

    from make_export_fixture import EMPTY_VIEW_ID

    directory = tmp_path / "corpus"
    directory.mkdir()

    def build(presentation):
        src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for name in src.namelist():
                data = src.read(name)
                if name == "views.zip":
                    inner = io.BytesIO()
                    with zipfile.ZipFile(io.BytesIO(data)) as views:
                        with zipfile.ZipFile(inner, "w") as w:
                            for member in views.namelist():
                                body = views.read(member)
                                if member.endswith("content.xml"):
                                    text = body.decode("utf-8")
                                    head, _sep, tail = text.partition(
                                        f'<ViewDef id="{EMPTY_VIEW_ID}"')
                                    before, _s2, after = tail.partition("</ViewDef>")
                                    before = before.replace(
                                        '<Presentation type="list"/>',
                                        f'<Presentation type="{presentation}"/>')
                                    text = head + _sep + before + _s2 + after
                                    body = text.encode("utf-8")
                                w.writestr(member, body)
                    data = inner.getvalue()
                z.writestr(name, data)
        return out.getvalue()

    (directory / "a.zip").write_bytes(build("list"))
    (directory / "b.zip").write_bytes(build("donut-chart"))

    both = corpus_census.walk(directory, "9.0.2")
    only_a = corpus_census.walk(directory, "9.0.2", [directory / "a.zip"])
    only_b = corpus_census.walk(directory, "9.0.2", [directory / "b.zip"])
    a_reasons = only_a["objects carrying nothing by reason"]
    b_reasons = only_b["objects carrying nothing by reason"]
    reasons = both["objects carrying nothing by reason"]
    assert a_reasons.get("view-no-columns", 0) == reasons.get("view-no-columns", 0) + 1
    assert b_reasons.get("view-no-attributes", 0) == reasons.get("view-no-attributes", 0) + 1
    assert both["divergent objects"] >= 1


def test_a_widget_whose_subject_differs_between_copies_is_excluded_too(tmp_path):
    """The fifth tie-break. One copy wires the widget, the other does not, so
    the same widget identity is "fed" in one and "self" in the other, and
    neither may reach the subject counts."""
    import io
    import json
    import zipfile

    from make_export_fixture import WIDGET_PROVIDER

    directory = tmp_path / "corpus"
    directory.mkdir()
    (directory / "a.zip").write_bytes(build_export_zip())
    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in src.namelist():
            data = src.read(name)
            if name.startswith("dashboards/"):
                inner = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(data)) as dash_zip:
                    with zipfile.ZipFile(inner, "w") as w:
                        for member in dash_zip.namelist():
                            body = dash_zip.read(member)
                            if member.endswith("dashboard.json"):
                                doc = json.loads(body)
                                for dash in doc["dashboards"]:
                                    dash["widgetInteractions"] = [
                                        entry for entry in dash.get("widgetInteractions", [])
                                        if entry.get("widgetIdProvider") != WIDGET_PROVIDER]
                                body = json.dumps(doc).encode()
                            w.writestr(member, body)
                data = inner.getvalue()
            z.writestr(name, data)
    (directory / "b.zip").write_bytes(out.getvalue())

    both = corpus_census.walk(directory, "9.0.2")
    only_a = corpus_census.walk(directory, "9.0.2", [directory / "a.zip"])
    only_b = corpus_census.walk(directory, "9.0.2", [directory / "b.zip"])
    a_subjects = only_a["widget subjects"]
    b_subjects = only_b["widget subjects"]
    subjects = both["widget subjects"]
    # Dropping the interaction changes two widget identities: the one that was
    # fed is no longer fed, and the one that drove it is no longer a selector.
    assert both["divergent widget subjects"] == 2
    # Neither copy's answer reaches the counts: a tie-break either way would
    # have kept one of them.
    assert subjects["fed"] == a_subjects["fed"] - 1
    assert subjects["selector"] == a_subjects["selector"] - 1
    assert subjects["never-shows"] == b_subjects["never-shows"] - 2
