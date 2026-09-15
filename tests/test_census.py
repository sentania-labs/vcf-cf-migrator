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
    """Two exports of one dashboard uuid where one copy's widget carries a
    metric and the other's does not, so the two copies classify the same
    widget identity differently."""
    import io
    import json
    import zipfile

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
                                for widget in doc["dashboards"][0]["widgets"]:
                                    if widget.get("title") == "[Fixture] CPU over time":
                                        # Same widget id, no metric in this copy.
                                        widget["config"]["metric"] = {
                                            "mode": "resourceKind",
                                            "resourceKindMetrics": [],
                                            "resourceMetrics": []}
                                body = json.dumps(doc).encode()
                            w.writestr(member, body)
                data = inner.getvalue()
            z.writestr(name, data)
    (directory / "b.zip").write_bytes(out.getvalue())
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
