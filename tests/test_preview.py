"""The preview: one page per kind, deterministic, and offline.

Everything here runs against the hand-built fixture export. Nothing reads the
corpus, and no value in this file comes from one.
"""
from __future__ import annotations

import re
import subprocess
import sys

import pytest

from make_export_fixture import (
    DASHBOARD_ID,
    DASHBOARD_ID_2,
    GROUP_NAME,
    OWNER,
    OWNER_2,
    REPORT_ID,
    SM_IDS,
    VIEW_IDS,
)
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import preview as _preview
from vcfcf_migrator.cli import main, preview_filename
from vcfcf_migrator.export_reader import read_members


@pytest.fixture
def built(export_zip):
    members = read_members(export_zip)
    return members, _graph.build_graph(members.data)


def node_for(graph, key_prefix):
    for node in graph.ordered():
        if node.key.startswith(key_prefix):
            return node
    raise AssertionError(f"no node starting {key_prefix} in {sorted(graph.nodes)}")


def page_for(graph, key_prefix):
    return _preview.render_page(graph, node_for(graph, key_prefix))


# ---------------------------------------------------------------------------
# Per kind
# ---------------------------------------------------------------------------

def test_dashboard_preview_lays_out_its_widgets(built):
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert "[Fixture] Cluster Overview" in page
    # The View widget resolves to the view it names and shows that view's
    # columns, which is the whole point of laying a dashboard out.
    assert "[Fixture] Cluster List" in page
    # The Scoreboard widget names a super metric key, which appears as a tile.
    assert f"Super Metric|sm_{SM_IDS[1]}" in page
    assert "SCOREBOARD" in page.upper()


def test_every_widget_type_the_preview_claims_is_exercised(built):
    """The fixture has to carry one widget of every type ``WIDGET_RENDERERS``
    knows, or a renderer ships with nothing executing it.

    This is the gate the milestone was missing: seven of the thirteen
    renderers drew 304 widgets in the real corpus and were run by no test, so
    the next change to any of them would have shipped green whatever it broke.
    A new entry in ``WIDGET_RENDERERS`` fails here until a widget for it lands
    in ``make_export_fixture._laid_out_widgets``.
    """
    _members, graph = built
    met = {}
    for node in graph.by_kind("dashboard"):
        preview = _preview.build(graph, node)
        for name, count in preview.widget_types.items():
            met[name] = met.get(name, 0) + count
    missing = set(_preview.HANDLED_WIDGETS) - set(met)
    assert missing == set(), f"no fixture widget exercises: {sorted(missing)}"
    # And the unhandled case is still present, so the other branch stays live.
    assert "Geo" in met


@pytest.mark.parametrize("title,expected", [
    ("[Fixture] CPU over time", "cpu|usage_average"),          # MetricChart
    ("[Fixture] Latency sparkline", "Virtual Disk|Read Latency"),  # SparklineChart
    ("[Fixture] Top consumers", "mem|consumed_average"),       # ParetoAnalysis
    ("[Fixture] Cluster heat", "cpu|usage_average"),           # Heatmap
    ("[Fixture] Cluster properties", "Cluster Configuration|DPM Enabled"),  # PropertyList
    ("[Fixture] Open alerts", "[Fixture] Cluster CPU alert"),  # AlertList, by name
    ("[Fixture] Clusters", "Memory|Usage"),                    # ResourceList
    ("[Fixture] Health", "badge|health"),                      # HealthChart
    ("[Fixture] Second half", "pv-section"),                   # Section
])
def test_each_laid_out_widget_draws_what_the_export_named(built, title, expected):
    """Each renderer puts the export's own label or key on the page. A
    renderer that starts drawing nothing, or somebody else's key, fails here
    rather than in a corpus run nobody repeats."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert title in page
    assert expected in page


def test_the_chart_widgets_draw_a_chart_and_the_heatmap_draws_cells(built):
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert page.count("<polyline") >= 2      # MetricChart and SparklineChart
    assert page.count("<rect") >= 8          # ParetoAnalysis, one per bar
    assert page.count("<div class='pv-heat'>") == 1
    assert "aria-label='mock trend'" in page and "aria-label='mock ranking'" in page


def test_dashboard_preview_names_a_widget_type_it_does_not_draw(built):
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    assert "Geo" in page
    assert "does not lay out this type" in page
    assert "widget types named rather than drawn: Geo x1" in page
    node = node_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    preview = _preview.build(graph, node)
    assert preview.unhandled_types == {"Geo": 1}
    # ProblemAlertsList and TextDisplay are laid out, so they are met but not
    # counted as unhandled.
    assert preview.widget_types["ProblemAlertsList"] == 1
    assert preview.widget_types["TextDisplay"] == 1


def test_a_widget_wider_than_the_declared_grid_widens_the_grid(built):
    """A widget the dashboard places past its own columns is not squashed into
    a sliver: the grid grows to fit it, and every other widget keeps its
    declared width."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    # The fixture's overhanging view sits at x=7 spanning 8, which needs 14.
    assert "grid-template-columns:repeat(14,1fr)" in preview.body
    assert "grid-column:7 / span 8" in preview.body
    assert any("drawn 14 columns wide" in note for note in preview.notes)
    # Nothing was clamped, so nothing claims it was moved.
    assert not any("is drawn at column" in note for note in preview.notes)


def test_a_widget_past_even_a_widened_grid_is_named_not_quietly_resized():
    """Past the widest grid this page will draw, the widget is clamped, and
    the note says which widget and what happened to it."""
    preview = _preview.Preview(node=None, title="", subtitle="", body="")
    cell = _preview._widget_cell(
        {"gridsterCoords": {"x": _preview.MAX_GRID_COLUMNS + 40, "y": 1, "w": 6, "h": 4}},
        "AlertList", "[Fixture] Far away", "", _preview.MAX_GRID_COLUMNS, preview)
    assert "grid-column:" in cell
    assert len(preview.notes) == 1
    assert "[Fixture] Far away" in preview.notes[0]
    assert "is drawn at column" in preview.notes[0]


def test_the_banner_says_whose_layout_this_is(built):
    """Heights are the page's, not the export's, and the page has to say so or
    an admin reads the proportions as the dashboard's."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert "widths and order are the" in page.lower()
    assert "heights are this page's" in page


def test_a_multi_tab_dashboard_names_the_tab_it_can_and_says_so_when_it_cannot(built):
    """No corpus dashboard has two tabs, so this is the only exercise the
    multi-tab heading gets. A tab the document names is named; a tab it does
    not name says the id is all there is, rather than printing the id as if it
    were a name."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    assert "tab [Fixture] Overview tab (2 widgets)" in page
    assert "tab id tab-two, which is all the document gives (2 widgets)" in page


@pytest.mark.parametrize("attrs,children,expected", [
    ({"type": "message_event", "operator": "contains", "eventType": "SYSTEM",
      "eventMsg": "disk failure"}, [], "disk failure"),
    ({"type": "log", "queryText": "error", "autoCancelTimeInMinutes": "0"},
     [("triggerCondition", {"function": "COUNT", "interval": "5",
                            "operator": "greaterThan", "value": "0.0"})], "log query"),
    ({"type": "somethingNew", "operator": "?"}, [], "does not state in words"),
])
def test_a_symptom_condition_is_stated_in_words_for_every_shape(attrs, children, expected):
    """The corpus carries metric, event and log conditions; a shape none of
    them carries says so rather than rendering as nothing."""
    import xml.etree.ElementTree as ET

    condition = ET.Element("Condition", attrs)
    for tag, child_attrs in children:
        ET.SubElement(condition, tag, child_attrs)
    words = _preview._condition_words(condition)
    assert expected in words


def test_text_widget_markup_is_shown_as_text_never_injected(built):
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    assert "<script>" not in page
    assert "alert(1)" in page  # as text, escaped, inside the widget
    assert "Read the runbook first" in page


def test_view_preview_shows_its_columns_with_mock_rows(built):
    _members, graph = built
    node = node_for(graph, f"view:{VIEW_IDS[0]}")
    page = _preview.render_page(graph, node)
    assert "[Fixture] Cluster List" in page
    assert f"Super Metric|sm_{SM_IDS[0]}" in page
    # One header row plus the mock rows.
    assert page.count("<tr>") >= _preview.MOCK_ROWS


def test_a_view_that_is_not_a_list_says_what_it_is(built):
    """A donut view has buckets this tool does not read, so drawing a donut
    would be drawing something the export did not say."""
    _members, graph = built
    page = page_for(graph, f"view:{VIEW_IDS[1]}")
    assert "donut-chart" in page
    assert "lays out list views only" in page


def test_a_dashboard_widget_embedding_a_non_list_view_says_so(built):
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    assert "lays out list views only" in page


def test_supermetric_preview_resolves_its_references_to_names(built):
    _members, graph = built
    page = page_for(graph, f"supermetric:{SM_IDS[0]}")
    # The formula as exported, and the same formula with the reference named.
    assert f"Super Metric|sm_{SM_IDS[1]}" in page
    assert "[Fixture] SM 2" in page
    assert "formula, with references resolved to names" in page


def test_supermetric_preview_marks_a_reference_the_export_does_not_carry(built):
    _members, graph = built
    page = page_for(graph, f"supermetric:{SM_IDS[2]}")
    assert "[Fixture] SM Nowhere" in page
    assert "not in this export" in page


def test_alert_preview_states_its_symptoms_and_recommendations_in_words(built):
    _members, graph = built
    page = page_for(graph, "alert:AlertDefinition-VMWARE-Fixture_Cluster_CPU")
    assert "the alert triggers when" in page
    assert "[Fixture] CPU high" in page          # the symptom, by name
    assert "Add hosts to the cluster" in page    # the recommendation, by name


def test_symptom_preview_states_its_condition_in_words(built):
    _members, graph = built
    page = page_for(graph, "symptom:SymptomDefinition-VMWARE-Fixture_CPU_high")
    assert f"Super Metric|sm_{SM_IDS[1]}" in page
    assert "static" in page


def test_report_preview_names_the_content_of_each_section(built):
    _members, graph = built
    page = page_for(graph, f"report:{REPORT_ID}")
    assert "[Fixture] Cluster List" in page       # the view section
    assert "[Fixture] Cluster Overview" in page   # the dashboard section


def test_customgroup_preview_states_its_rules(built):
    _members, graph = built
    page = page_for(graph, f"customgroup:{GROUP_NAME}")
    assert "RelationshipRule" in page
    assert "ResourceNameRule" in page


@pytest.mark.parametrize("prefix", [
    f"dashboard:{DASHBOARD_ID}@{OWNER}",
    f"view:{VIEW_IDS[0]}",
    f"supermetric:{SM_IDS[0]}",
    "alert:AlertDefinition-VMWARE-Fixture_Cluster_CPU",
    "symptom:SymptomDefinition-VMWARE-Fixture_CPU_high",
    "recommendation:",
    f"report:{REPORT_ID}",
    f"customgroup:{GROUP_NAME}",
    "notificationrule:",
    "notificationtemplate:",
    "outboundsetting:",
])
def test_every_kind_previews(built, prefix):
    _members, graph = built
    page = page_for(graph, prefix)
    assert page.startswith("<!doctype html>")
    assert "</html>" in page


# ---------------------------------------------------------------------------
# The two properties the spec asks for: deterministic, and offline
# ---------------------------------------------------------------------------

def test_the_same_export_previews_byte_identically(built):
    _members, graph = built
    for node in graph.ordered():
        first = _preview.render_page(graph, node)
        second = _preview.render_page(graph, node)
        assert first == second, node.key


def test_a_second_process_previews_byte_identically(export_zip, tmp_path):
    """Determinism has to survive a fresh interpreter, which is where a value
    seeded from Python's per-process string hash would show up."""
    def run(out):
        code = subprocess.run(
            [sys.executable, "-m", "vcfcf_migrator", "preview", str(export_zip),
             f"dashboard:{DASHBOARD_ID}@{OWNER}", "--out", str(out)],
            capture_output=True, text=True)
        assert code.returncode == 0, code.stderr
        return out.read_bytes()

    assert run(tmp_path / "one.html") == run(tmp_path / "two.html")


def test_the_page_reaches_no_network(built):
    _members, graph = built
    for node in graph.ordered():
        page = _preview.render_page(graph, node)
        # No remote anything: no scheme, no script tag, no external reference.
        assert "http://" not in page and "https://" not in page, node.key
        assert "<script" not in page.lower(), node.key
        assert "<img" not in page.lower(), node.key
        assert not re.search(r"\bsrc\s*=", page), node.key
        assert "@import" not in page, node.key


def test_mock_values_come_from_the_key_not_the_clock():
    from vcfcf_migrator import mockdata

    assert mockdata.metric_value("cpu|usage_average", "percent") == \
        mockdata.metric_value("cpu|usage_average", "percent")
    assert mockdata.metric_value("cpu|usage_average", "percent") != \
        mockdata.metric_value("mem|usage_average", "percent")
    percent = float(mockdata.metric_value("x", "percent").replace(",", ""))
    assert 0 <= percent <= 100
    assert mockdata.series("k", points=8) == mockdata.series("k", points=8)
    assert len(mockdata.series("k", points=8)) == 8


def test_widget_text_survives_an_angle_bracket_in_an_attribute_and_an_entity():
    """A depth counter over < and > leaks attribute text into the words the
    admin reads, and leaves &amp; on screen as five characters."""
    text = _preview._strip_tags(
        '<a title="1 > 0" href="x">Fix&nbsp;the&amp;check</a><br/>now')
    assert "title=" not in text and "href" not in text
    assert "1 > 0" not in text
    assert "Fix" in text and "check" in text and "&amp;" not in text
    assert text.endswith("now")


def test_a_preview_of_a_missing_document_is_refused(built):
    _members, graph = built
    node = node_for(graph, f"view:{VIEW_IDS[0]}")
    node.index = 99
    with pytest.raises(_preview.PreviewError):
        _preview.render_page(graph, node)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def test_cli_preview_writes_a_file_and_prints_its_path(export_zip, tmp_path, capsys):
    out = tmp_path / "one.html"
    assert main(["preview", str(export_zip), VIEW_IDS[0], "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip() == str(out)
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_cli_preview_prints_to_stdout(export_zip, capsys):
    assert main(["preview", str(export_zip), VIEW_IDS[0], "--print"]) == 0
    assert capsys.readouterr().out.startswith("<!doctype html>")


def test_cli_preview_defaults_the_filename_to_the_object(export_zip, tmp_path, monkeypatch,
                                                         capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["preview", str(export_zip), VIEW_IDS[0]]) == 0
    written = capsys.readouterr().out.strip()
    assert written == f"preview-view-{VIEW_IDS[0]}.html"
    assert (tmp_path / written).exists()


def test_cli_preview_refuses_an_unwritable_path_with_a_message(export_zip, tmp_path, capsys):
    """Every other refusal in this CLI is a message and an exit code, so an
    unwritable path must not be the one that gives a traceback."""
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory")
    assert main(["preview", str(export_zip), VIEW_IDS[0],
                 "--out", str(blocked / "nested" / "p.html")]) == 1
    assert "cannot write" in capsys.readouterr().err


def test_cli_preview_refuses_an_object_the_export_does_not_carry(export_zip, capsys):
    assert main(["preview", str(export_zip), "not-a-real-uuid"]) == 1
    assert "carries no object" in capsys.readouterr().err


def test_cli_preview_refuses_an_ambiguous_object_and_says_the_spellings(export_zip, capsys):
    """The shared dashboard uuid is two objects, one per owner. Picking one
    would preview something the admin did not ask for."""
    assert main(["preview", str(export_zip), DASHBOARD_ID]) == 1
    err = capsys.readouterr().err
    assert "names 2 objects" in err
    assert OWNER in err and OWNER_2 in err


def test_cli_preview_accepts_the_owner_spelling(export_zip, tmp_path, capsys):
    out = tmp_path / "d.html"
    assert main(["preview", str(export_zip), f"dashboard:{DASHBOARD_ID}@{OWNER_2}",
                 "--out", str(out)]) == 0
    assert "[Fixture] Cluster Overview" in out.read_text(encoding="utf-8")


def test_preview_filename_is_safe_for_a_file_system(built):
    _members, graph = built
    node = node_for(graph, f"customgroup:{GROUP_NAME}")
    name = preview_filename(node)
    assert "/" not in name and "\\" not in name and " " not in name
    assert name.startswith("preview-customgroup-") and name.endswith(".html")
