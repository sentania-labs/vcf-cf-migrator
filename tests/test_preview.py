"""The preview: one page per kind, deterministic, and offline.

Everything here runs against the hand-built fixture export. Nothing reads the
corpus, and no value in this file comes from one.
"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import Dict

import pytest

from make_export_fixture import (
    DASHBOARD_ID,
    DASHBOARD_ID_2,
    EMPTY_SM_ID,
    EMPTY_VIEW_ID,
    GROUP_NAME,
    GROUP_NAME_3,
    OWNER,
    OWNER_2,
    REPORT_ID,
    SM_IDS,
    VIEW_IDS,
    WIDGET_ORPHAN,
    WIDGET_PROVIDER,
    WIDGET_RECEIVER,
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
    ("[Fixture] Second half", "[Fixture] Second half"),        # Section, see below
])
def test_each_laid_out_widget_draws_what_the_export_named(built, title, expected):
    """Each renderer puts the export's own label or key on the page. A
    renderer that starts drawing nothing, or somebody else's key, fails here
    rather than in a corpus run nobody repeats."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert title in page
    assert expected in page


def test_the_section_renderer_draws_a_divider_and_nothing_else(built):
    """A Section is a divider: its renderer deliberately returns no body, and
    the frame carries the heading alone. Asserting the frame's class would
    assert what ``_widget_cell`` derives from the widget type, which is true
    whatever the renderer does."""
    assert _preview.WIDGET_RENDERERS["Section"]({"title": "x"}, {}) == ""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    row = re.search(r"<div class='pv-section'[^>]*>(.*?)</div>", page, re.S)
    assert row, "the Section widget should render as a divider row"
    assert "[Fixture] Second half" in row.group(1)
    # Heading only: no table, no chart, no placeholder inside the divider.
    assert not re.search(r"<(table|svg|div)\b", row.group(1))


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


def test_a_heatmap_draws_in_the_products_heat_colours_never_in_the_pages_blue(built):
    """VCF Operations heat scales run red to green and never show blue; the
    page's blue is chart ink and says nothing about a value."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    heat = page.split("<div class='pv-heat'>")[1].split("</div>")[0]
    assert _preview.HEAT_GOOD in heat or _preview.HEAT_BAD in heat
    assert "--pv-accent" not in heat and "--pv-ok" not in heat


def test_a_heatmap_keeps_the_colours_the_widget_declares():
    """The scale runs either way round depending on the metric, so the widget's
    own list wins over the default ramp."""
    reversed_scale = ["#DE3F30", "#ECC33E", "#74B43B"]
    cfg = {"configs": [{"colorBy": "cpu|demandPct",
                        "color": {"thresholds": {"values": [0, 50, 100],
                                                 "colors": reversed_scale}}}]}
    assert _preview.heatmap_palette(cfg) == tuple(reversed_scale)
    assert _preview.heatmap_palette({"configs": [{}]}) == _preview.HEAT_RAMP
    # Anything that is not a plain hex colour is ignored rather than injected.
    assert _preview.heatmap_palette(
        {"configs": [{"color": {"thresholds": {"colors": ["url(x)", 3]}}}]}
    ) == _preview.HEAT_RAMP


def test_a_health_chart_does_not_claim_health_it_has_not_read(built):
    """Its values are invented, so drawing it in the product's healthy green
    would be a verdict this page cannot reach."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    body = page.split("<body>")[1]
    cell = body.split("HealthChart</span></h3>")[1][:900]
    assert "polyline" in cell
    assert "--pv-ok" not in cell


@pytest.mark.parametrize("value,label,key", [
    ({"metricKey": "cpu|demandPct", "value": "CPU Demand %"}, "CPU Demand %", "cpu|demandPct"),
    ("cpu|usage_average", "", "cpu|usage_average"),
    ({}, "", ""),
    (None, "", ""),
])
def test_a_metric_reference_reads_as_a_pair_or_as_a_bare_key(value, label, key):
    assert _preview.metric_ref(value) == (label, key)


def test_a_heatmap_names_its_metric_rather_than_printing_the_document(built):
    """The corpus writes colorBy as {metricKey, value}; ``str()`` on it put the
    braces and quotes on the page under every heatmap."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert "CPU Usage %" in page and "cpu|usage_average" in page
    assert "{&#x27;metricKey&#x27;" not in page and "{'metricKey'" not in page


def test_a_heatmap_whose_block_names_no_metric_carries_nothing():
    cfg = {"configs": [{"colorBy": {}, "sizeBy": None}]}
    code, _sentence = _preview._widget_nothing("Heatmap", cfg, {"type": "Heatmap"})
    assert code == "widget-no-heatmap-metric"
    assert _preview.heatmap_metrics(cfg) == []


def test_a_text_widget_reads_its_words_from_editor_data_not_from_the_flag(built):
    """``viewModeHTML`` is a flag saying the words are markup, not the words.

    Every TextDisplay in the corpus carries it as the boolean ``True`` with the
    text in ``editorData``, and reading it as content drew all 25 of them as
    the single word "True".
    """
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert "Cluster headroom is measured after HA." in page
    assert ">True<" not in page


def test_a_flag_over_an_empty_body_is_a_text_widget_with_no_text(built):
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    empty = [title for title, _kind, reason in preview.empty_widgets
             if "carries no text" in reason]
    assert "[Fixture] Flagged but empty" in empty, preview.empty_widgets
    assert "[Fixture] About these metrics" not in empty


@pytest.mark.parametrize("cfg,expected", [
    ({"viewModeHTML": True, "editorData": "<p>words</p>"}, "words"),
    ({"viewModeHTML": False, "editorData": "plain words"}, "plain words"),
    ({"viewModeHTML": "<p>markup here</p>"}, "markup here"),
    ({"viewModeHTML": True, "editorData": ""}, ""),
    ({"viewModeHTML": True}, ""),
    ({"editorData": {"blocks": ["structured"]}}, "structured"),
])
def test_text_widget_content_reads_every_shape_the_key_pair_is_written_in(cfg, expected):
    assert expected in _preview.text_widget_words(cfg)


def test_the_renderer_and_the_emptiness_rule_read_the_text_the_same_way():
    """One reader, so the page cannot draw words the roll-up calls missing."""
    cfg = {"viewModeHTML": True, "editorData": ""}
    code, _sentence = _preview._widget_nothing("TextDisplay", cfg, {"type": "TextDisplay"})
    assert code == "widget-no-text"
    assert _preview.text_widget_words(cfg) == ""


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


# ---------------------------------------------------------------------------
# Never an empty preview: the three states
# ---------------------------------------------------------------------------

def test_no_preview_renders_a_box_that_says_nothing(built):
    """The rule, asserted over every object the fixture carries: a widget
    frame always has a body, and that body always says something."""
    _members, graph = built
    frame = re.compile(r"<div class='pv-w[^']*'[^>]*>(.*?)</h3>(.*?)(?=<div class='pv-w|$)",
                       re.S)
    for node in graph.ordered():
        page = _preview.render_page(graph, node)
        for _head, body in frame.findall(page):
            text = re.sub(r"<[^>]+>", "", body).strip()
            assert text, f"{node.key} has a widget frame with an empty body"


def test_an_empty_widget_of_an_undrawn_type_is_empty_first(built):
    """The Skittles case. State 3 beats state 2: the fact the admin can act on
    is that the widget carries no configuration, not that this page does not
    draw Skittles."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    empty = {title: reason for title, _kind, reason in preview.empty_widgets}
    assert "[Fixture] Unfinished widget" in empty
    assert "carries no configuration at all" in empty["[Fixture] Unfinished widget"]
    # And it is not counted as a type this page cannot draw.
    assert "Skittles" not in preview.unhandled_types
    page = _preview.render_page(graph, node)
    assert "nothing to show" in page
    # The wording is about the content, never about the tool.
    for _title, _kind, reason in preview.empty_widgets:
        assert "this preview" not in reason.lower()
        assert "lay out" not in reason.lower()


def test_an_empty_widget_of_a_drawn_type_says_what_is_missing(built):
    _members, graph = built
    preview = _preview.build(graph, node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}"))
    empty = {title: reason for title, _kind, reason in preview.empty_widgets}
    assert "a title and nothing else" in empty["[Fixture] Empty scoreboard"]


def test_the_notes_count_the_empty_widgets(built):
    _members, graph = built
    preview = _preview.build(graph, node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}"))
    note = [n for n in preview.notes if "with nothing to show" in n]
    assert note, preview.notes
    assert str(len(preview.empty_widgets)) in note[0]
    assert "[Fixture] Unfinished widget" in note[0]


def test_the_three_states_are_distinguishable_at_a_glance(built):
    """Drawn, named-but-not-drawn, and empty each get their own markup, so a
    reader can tell them apart without reading the sentences."""
    _members, graph = built
    # State 3 and state 1 live on the Cluster Overview, state 2 (a Geo widget
    # that does carry a configuration) on the VM Overview.
    drawn_and_empty = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    named = page_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    assert "class='pv-nothing'" in drawn_and_empty   # state 3
    assert "class='pv-tbl'" in drawn_and_empty       # state 1
    assert "class='pv-placeholder'" in named         # state 2
    assert "does not lay out this type" in named
    # And the three never collapse into one another's markup.
    assert "class='pv-nothing'" not in named


@pytest.mark.parametrize("prefix,expected", [
    (f"view:{EMPTY_VIEW_ID}", "declares no columns"),
    (f"supermetric:{EMPTY_SM_ID}", "empty formula"),
    (f"customgroup:{GROUP_NAME_3}", "no membership rules"),
])
def test_an_object_carrying_nothing_says_so_in_its_own_words(built, prefix, expected):
    _members, graph = built
    node = node_for(graph, prefix)
    preview = _preview.build(graph, node)
    assert preview.empty_reason, f"{prefix} should be state 3"
    assert expected in preview.empty_reason
    assert "nothing to show" in _preview.render_page(graph, node)
    assert "this preview" not in preview.empty_reason.lower()


def test_an_empty_object_is_not_described_as_a_tool_limitation(built):
    _members, graph = built
    for node in graph.ordered():
        preview = _preview.build(graph, node)
        if preview.empty_reason:
            assert "does not lay out" not in preview.empty_reason


# ---------------------------------------------------------------------------
# Relationships: which widget drives which
# ---------------------------------------------------------------------------

def test_the_wiring_is_read_from_the_document(built):
    _members, graph = built
    doc = _preview._json_doc(
        _preview.raw_document(graph, node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")))
    wiring = _preview.read_wiring(doc, doc["widgets"])
    assert wiring.providers[WIDGET_RECEIVER] == [(WIDGET_PROVIDER, "resourceId")]
    assert wiring.receivers[WIDGET_PROVIDER] == [(WIDGET_RECEIVER, "resourceId")]
    assert wiring.title(WIDGET_PROVIDER) == "[Fixture] Clusters"


def test_a_receiver_names_its_provider_and_a_provider_says_what_it_feeds(built):
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert "driven by [Fixture] Clusters" in page
    assert "drives 1 widget" in page
    assert "is-receiver" in page and "is-provider" in page
    # The flow is visible above the layout, not only inside the boxes.
    assert "This dashboard is interaction driven" in page
    assert "[Fixture] Clusters</b> <span class='arrow'>drives</span> " \
           "[Fixture] CPU over time" in page


def test_a_receiver_labels_whose_selection_its_mock_values_stand_for(built):
    """The chosen presentation: the widget is drawn, so its columns and
    metrics can be recognised, and the values are labelled as standing for one
    object the provider would supply."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    assert "values below stand for one object picked in [Fixture] Clusters" in page
    assert "shows nothing until that selection is made" in page


def test_a_receiver_nothing_feeds_will_never_show_data(built):
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    empty = {title: reason for title, _kind, reason in preview.empty_widgets}
    assert "[Fixture] Orphaned trend" in empty
    # The widget the fixture marks as the orphan, by id, so renaming the title
    # cannot quietly make this test assert nothing.
    doc = _preview._json_doc(_preview.raw_document(graph, node))
    orphan = [w for w in doc["widgets"] if w.get("id") == WIDGET_ORPHAN]
    assert orphan and _preview._widget_title(orphan[0]) == "[Fixture] Orphaned trend"
    assert "nothing on this dashboard feeds it" in empty["[Fixture] Orphaned trend"]
    assert "will never show data" in empty["[Fixture] Orphaned trend"]
    assert preview.orphan_receivers == 1
    assert any("never show data" in note for note in preview.notes)


def test_the_notes_count_the_widgets_driven_by_a_selection(built):
    _members, graph = built
    preview = _preview.build(graph, node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}"))
    # Four wired pairs: the cluster list driving the chart, the bare selector
    # driving the property list, and the two selectors whose own renderers
    # find nothing to draw driving the heatmap and the health chart.
    assert preview.receivers == 4 and preview.providers == 4
    assert any("driven by the object picked in another widget" in n for n in preview.notes)


def test_a_dashboard_with_no_interactions_says_nothing_about_wiring(built):
    """The VM Overview dashboard has no widgetInteractions, so no flow block,
    no badges, and no note about selections."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    preview = _preview.build(graph, node)
    page = _preview.render_page(graph, node)
    assert preview.receivers == 0 and preview.providers == 0
    assert "This dashboard is interaction driven" not in page
    assert "class='pv-flow" not in page
    assert not any("driven by the object picked" in n for n in preview.notes)


def test_wiring_never_claims_a_relationship_it_cannot_name_on_both_ends(built):
    """An interaction naming a widget that is not in the document is dropped
    rather than rendered as a relationship to something unnamed. No corpus
    entry does this; the guard is what keeps a future one honest."""
    _members, graph = built
    doc = _preview._json_doc(
        _preview.raw_document(graph, node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")))
    doc["widgetInteractions"] = [
        {"widgetIdProvider": WIDGET_PROVIDER, "type": "resourceId",
         "widgetIdReceiver": "not-a-widget-in-this-document"}]
    wiring = _preview.read_wiring(doc, doc["widgets"])
    assert wiring.providers == {} and wiring.receivers == {}


def test_a_receiver_on_an_unwired_dashboard_takes_its_subject_from_outside(built):
    """Two different situations wear the same two keys, and the corpus
    separates them cleanly. A dashboard that wires nothing at all and whose
    widgets all wait on a selection is a dashboard opened in an object's
    context, not a broken one: 117 widgets in the corpus are like that, whole
    summary-style dashboards where seven of eight widgets wait. Calling those
    broken would be inventing a fault."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}")
    preview = _preview.build(graph, node)
    assert preview.context_driven == 1
    assert preview.orphan_receivers == 0
    page = _preview.render_page(graph, node)
    assert "takes its subject from a selection made outside this dashboard" in page
    assert any("from outside this dashboard" in note for note in preview.notes)


def test_the_nested_and_the_flat_self_provider_are_both_read():
    """Every corpus widget nests it; the flat boolean is what the key's name
    implies. Reading only the flat form treated 674 real widgets as 'not
    stated', which would have missed the case this rule exists for."""
    assert _preview._self_provider({"selfProvider": {"selfProvider": False}}) is False
    assert _preview._self_provider({"selfProvider": False}) is False
    assert _preview._self_provider({"selfProvider": {"selfProvider": True}}) is True
    assert _preview._self_provider({"selfProvider": "false"}) is False
    assert _preview._self_provider({}) is None
    assert _preview._self_provider({"selfProvider": {"other": 1}}) is None


# ---------------------------------------------------------------------------
# The gate: every "carries nothing" branch, and every subject classification,
# has a fixture document behind it
# ---------------------------------------------------------------------------

def _all_previews(graph):
    return [(node, _preview.build(graph, node)) for node in graph.ordered()]


def test_every_widget_empty_reason_has_a_fixture_widget(built):
    """The same shape as the widget-renderer gate. A new branch in
    ``_widget_nothing`` fails here until a fixture widget exercises it: the
    first version of the never-shows rule called 27 selectors broken and no
    test touched the classification at all."""
    _members, graph = built
    met = set()
    for _node, preview in _all_previews(graph):
        met.update(preview.empty_codes)
    missing = set(_preview.WIDGET_EMPTY_CODES) - met
    assert missing == set(), f"no fixture widget exercises: {sorted(missing)}"
    assert met <= set(_preview.WIDGET_EMPTY_CODES), sorted(met)


def test_every_object_empty_reason_has_a_fixture_object(built):
    _members, graph = built
    met = {preview.empty_code for _node, preview in _all_previews(graph) if preview.empty_code}
    missing = set(_preview.OBJECT_EMPTY_CODES) - met
    assert missing == set(), f"no fixture object exercises: {sorted(missing)}"


def test_every_subject_classification_has_a_fixture_widget(built):
    """Self, fed, selector, from-outside and never-shows. The classification
    that got the selector wrong is the one this pins."""
    _members, graph = built
    met: Dict[str, int] = {}
    for _node, preview in _all_previews(graph):
        for kind, count in preview.subjects.items():
            met[kind] = met.get(kind, 0) + count
    assert set(_preview.SUBJECT_KINDS) - set(met) == set(), sorted(met)


def test_a_selector_is_not_called_broken(built):
    """A widget with selfProvider false that drives other widgets is the
    dashboard's selector, not a widget that will never show data. 27 of the 32
    the first rule flagged were exactly this, and one of them carried a badge
    reading "drives 9 widgets" directly above "it will never show data"."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    page = _preview.render_page(graph, node)
    titles = {title for title, _kind, _reason in preview.empty_widgets}
    assert "[Fixture] Clusters" not in titles, "the selector must not be state 3"
    # Four selectors: the cluster list, the one whose own config is empty,
    # and the two whose renderers find nothing of their own to draw.
    assert preview.selectors == 4
    assert "this widget drives 1 widget on this dashboard" in page
    # And the one genuinely unfed receiver is still called out.
    assert "[Fixture] Orphaned trend" in titles
    assert preview.orphan_receivers == 1


def test_a_widget_that_will_never_show_data_is_still_drawn(built):
    """The sentence is a caption over the widget, not a replacement for it:
    a blank box shows neither its columns nor its metrics, which is the same
    argument the fed-receiver case already makes."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    frame = re.search(r"<h3>\[Fixture\] Orphaned trend.*?(?=<div class='pv-w)", page, re.S)
    assert frame, "the orphaned widget should have a frame"
    assert "will never show data" in frame.group(0)
    assert "<polyline" in frame.group(0), "the widget itself should still be drawn"


def test_every_state_three_box_on_the_page_is_counted(built):
    """The roll-up has to count what the page shows: three boxes used to
    bypass the accumulator, so the notes were silent about them."""
    _members, graph = built
    for node in graph.by_kind("dashboard"):
        preview = _preview.build(graph, node)
        boxes = preview.body.count("class='pv-nothing'")
        counted = len(preview.empty_widgets) + (1 if preview.empty_reason else 0)
        assert boxes == counted, f"{node.key}: {boxes} boxes, {counted} counted"


# ---------------------------------------------------------------------------
# What the tool must not call broken
# ---------------------------------------------------------------------------

def test_a_widget_whose_layout_lives_in_its_saved_state_is_not_called_empty(built):
    """VCF Operations stores a resource list's and an alert list's column
    layout in ``states[].value``, not in ``config``. A widget with an empty
    config and a four kilobyte state blob is configured, and calling it
    unconfigured was simply false."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    titles = {title for title, _kind, _reason in preview.empty_widgets}
    assert "[Fixture] Layout kept in state" not in titles
    codes = {code for _t, _k, code, _r in preview.elsewhere}
    assert "widget-state-not-read" in codes
    page = _preview.render_page(graph, node)
    assert "stores this widget" in page and "in its saved state" in page
    assert any("keep their layout in the saved state" in note for note in preview.notes)


def test_a_selector_with_an_empty_config_is_still_a_selector(built):
    """The wiring decides before the configuration does. 48 widgets in the
    corpus drive other widgets while carrying a config of {}, and 24 of them
    rendered a badge saying how many widgets they drive directly above a box
    saying they show nothing."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    titles = {title for title, _kind, _reason in preview.empty_widgets}
    assert "[Fixture] Bare selector" not in titles
    assert {t for t, _k, _c, _r in preview.elsewhere} != {"[Fixture] Bare selector"}
    page = _preview.render_page(graph, node)
    # The badge and the body agree: it drives, and it is not called empty.
    frame = re.search(r"<h3>\[Fixture\] Bare selector.*?(?=<div class='pv-w)", page, re.S)
    assert frame and "drives 1 widget" in frame.group(0)
    assert "nothing to show" not in frame.group(0)
    assert "this widget drives 1 widget on this dashboard" in frame.group(0)


def test_no_frame_claims_nothing_to_show_while_claiming_to_drive_widgets(built):
    """The contradiction, asserted over every dashboard in the fixture."""
    _members, graph = built
    for node in graph.by_kind("dashboard"):
        page = _preview.render_page(graph, node)
        for frame in re.findall(r"<div class='pv-w[^']*'[^>]*>(.*?)(?=<div class='pv-w|$)",
                                page, re.S):
            assert not ("drives" in frame and "nothing to show" in frame), node.key


def test_a_view_the_export_does_not_carry_is_not_called_nothing(built):
    """An export declares type=CUSTOM and carries custom content only, so a
    widget naming a view that ships in a management pack points outside every
    export by construction: 99 such view uuids across the corpus, 75 of them
    resolvable to a pak in the factory's reference tree. The page says the
    target may already have it, and says it cannot tell the two apart."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    titles = {title for title, _kind, _reason in preview.empty_widgets}
    assert "[Fixture] View widget naming an absent view" not in titles
    codes = [code for _t, _k, code, _r in preview.elsewhere]
    assert codes.count("widget-view-not-carried") == 2
    page = _preview.render_page(graph, node)
    assert "which this export does not carry" in page
    assert "will show whatever the target already has" in page
    assert "an export cannot tell the two apart" in page
    assert any("show a view this export does not carry" in note for note in preview.notes)


def test_every_elsewhere_reason_has_a_fixture_widget(built):
    """The same gate as the empty codes, for the two states that are not
    emptiness."""
    _members, graph = built
    met = set()
    for _node, preview in _all_previews(graph):
        met.update(code for _t, _k, code, _r in preview.elsewhere)
    assert set(_preview.WIDGET_ELSEWHERE_CODES) - met == set(), sorted(met)


def test_the_roll_up_prints_one_clause_per_reason_not_per_widget(built):
    """The sentence for a view-shaped reason carries a uuid, so grouping on it
    printed one clause per widget."""
    _members, graph = built
    preview = _preview.build(graph, node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}"))
    note = [n for n in preview.notes if "with nothing to show" in n][0]
    clauses = note.split(": ", 1)[1].split(" (")[0].split("; ")
    assert len(clauses) == len({c.split(" where ", 1)[1] for c in clauses})
    assert len(clauses) <= len(_preview.WIDGET_EMPTY_CODES)


def test_a_wiring_line_names_as_many_widgets_as_the_badge_counts(built):
    """Two receivers sharing a title are two widgets. Deduping on the title
    lost one, so the line said "drives 2 widgets" and named one."""
    _members, graph = built
    for node in graph.by_kind("dashboard"):
        page = _preview.render_page(graph, node)
        for line in re.findall(r"<li><b>(.*?)</li>", page, re.S):
            named = len(re.sub(r"<[^>]+>", "", line).split("drives", 1)[-1].split(", "))
            assert named >= 1
        for badge in re.findall(r"drives (\d+) widget", page):
            assert int(badge) >= 1
    # The fixture's providers each drive exactly one widget.
    preview = _preview.build(graph, node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}"))
    assert preview.providers == 4


def test_a_selector_that_declares_it_picks_its_own_subject_is_not_told_otherwise(built):
    """72 of the corpus's 92 selectors declare selfProvider true: they drive
    other widgets and choose their own subject. The caption used to say
    "chooses no subject of its own" about all 92."""
    _members, graph = built
    page = page_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    frame = re.search(r"<h3>\[Fixture\] Driving scoreboard.*?(?=<div class='pv-w)", page, re.S)
    assert frame
    assert "drives 1 widget on this dashboard, and picks its own subject" in frame.group(0)
    assert "chooses no subject of its own" not in frame.group(0)
    # And the selector the export says does not choose its own subject says
    # what the export says, which is that nothing feeds it: not that it takes
    # its subject from the widgets it drives, which is the wiring backwards.
    fed = re.search(r"<h3>\[Fixture\] Clusters.*?(?=<div class='pv-w)", page, re.S)
    assert fed
    assert ("the export says it does not choose its own subject and names nothing that "
            "feeds it") in fed.group(0)
    assert "shows whatever is picked in them" not in page
    assert "picks its own subject" not in fed.group(0)


@pytest.mark.parametrize("title", ["[Fixture] Driving scoreboard", "[Fixture] Driving view"])
def test_a_driving_widget_is_never_told_it_shows_nothing(built, title):
    """The renderers hold this, not the caller: a scoreboard with an empty
    metric block and a view widget naming a columnless view both find nothing
    to draw, and both drive another widget, so neither may say it shows
    nothing under a badge counting what it drives."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    assert title not in {t for t, _k, _r in preview.empty_widgets}
    codes = {code for t, _k, code, _r in preview.elsewhere if t == title}
    assert codes == {"widget-drives-only"}
    page = _preview.render_page(graph, node)
    frame = re.search(rf"<h3>{re.escape(title)}.*?(?=<div class='pv-w)", page, re.S)
    assert frame and "drives" in frame.group(0)
    assert "nothing to show" not in frame.group(0)
    assert "carries no content of its own to draw here" in frame.group(0)


def test_a_state_holding_only_the_empty_object_marker_is_not_configuration(built):
    """Three corpus widgets carry a state whose whole value is ``o%3A``,
    which unquotes to ``o:``: an ExtJS object with no fields. Telling their
    admin they were configured was as wrong as telling the others they were
    not. The grammar is in the factory's own wire-format note."""
    from make_export_fixture import WIDGET_EMPTY_STATE

    assert _preview.widget_state_blob({"states": [{"value": "o%3A"}]}) == ""
    assert _preview.widget_state_blob({"states": [{"value": "o%3Acolumns%3Da%253A"}]}) != ""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    preview = _preview.build(graph, node)
    empty = {title: reason for title, _kind, reason in preview.empty_widgets}
    assert "[Fixture] Empty state blob" in empty
    assert "no stored state either" in empty["[Fixture] Empty state blob"]
    doc = _preview._json_doc(_preview.raw_document(graph, node))
    assert any(w.get("id") == WIDGET_EMPTY_STATE for w in doc["widgets"])


def test_the_subject_rule_lives_in_one_function(built):
    """The census used to carry its own copy and had already drifted from
    this one."""
    assert _preview.subject_of({}, feeds=True, driven=False,
                               dashboard_has_wiring=True) == "selector"
    assert _preview.subject_of({}, feeds=True, driven=True,
                               dashboard_has_wiring=True) == "fed"
    cfg = {"selfProvider": {"selfProvider": False}}
    assert _preview.subject_of(cfg, False, False, True) == "never-shows"
    assert _preview.subject_of(cfg, False, False, False) == "from-outside"
    assert _preview.subject_of({}, False, False, True) == "self"


def test_a_selector_the_export_says_nothing_about_gets_neither_tail(built):
    """The third branch, and the only one where a regression invents a claim
    rather than repeating one: where the export declares no selfProvider, the
    caption says the widget drives and stops. Four fixture selectors are
    shaped this way."""
    _members, graph = built
    node = node_for(graph, f"dashboard:{DASHBOARD_ID}@{OWNER}")
    page = _preview.render_page(graph, node)
    frame = re.search(r"<h3>\[Fixture\] Bare selector.*?(?=<div class='pv-w)", page, re.S)
    assert frame
    body = frame.group(0)
    assert "drives 1 widget on this dashboard" in body
    assert "picks its own subject" not in body
    assert "does not choose its own subject" not in body
    # The caption ends at the driving clause: nothing is claimed about a
    # subject the export says nothing about.
    caption = re.search(r"<p class='pv-drivenby'>(.*?)</p>", body, re.S)
    assert caption and caption.group(1).endswith("on this dashboard")


def test_every_selector_caption_matches_what_the_export_declares(built):
    """All three branches at once, over every fixture dashboard, matched as a
    multiset rather than by locating each widget's frame by its title: one
    title that is a prefix of another finds the wrong frame, which is a
    weakness of the check and not of the page.
    """
    import collections

    _members, graph = built
    for node in graph.by_kind("dashboard"):
        preview = _preview.build(graph, node)
        doc = _preview._json_doc(_preview.raw_document(graph, node))
        widgets = [w for w in doc.get("widgets", []) if isinstance(w, dict)]
        wiring = _preview.read_wiring(doc, widgets)
        keys = _preview.widget_keys(widgets)
        declared = collections.Counter()
        for widget in widgets:
            key = keys[id(widget)]
            if not wiring.receivers.get(key) or wiring.providers.get(key):
                continue
            cfg = widget.get("config") if isinstance(widget.get("config"), dict) else {}
            says = _preview._self_provider(cfg)
            declared["true" if says is True else "false" if says is False else "unsaid"] += 1
        captions = collections.Counter()
        for caption in re.findall(r"<p class='pv-drivenby'>(.*?)</p>",
                                  _preview.render_page(graph, node), re.S):
            if "drives" not in caption:
                continue
            captions["true" if "picks its own subject" in caption else
                     "false" if "does not choose its own subject" in caption
                     else "unsaid"] += 1
        assert captions == declared, node.key
        assert preview.selectors == sum(declared.values())



# ---------------------------------------------------------------------------
# A declared flag is read, not guessed from truthiness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    ("true", True), ("false", False), ("True", True), ("FALSE", False),
    ("yes", True), ("no", False), ("1", True), ("0", False), ("on", True), ("off", False),
    (" true ", True), ("", None), ("maybe", None), (None, None), ([], None), ({}, None),
    (1, True), (0, False),
])
def test_a_declared_flag_is_read_from_every_spelling(value, expected):
    """XML hands every attribute back as a string, so "false" is truthy, and
    JSON writes real booleans today with nothing stopping a future export
    writing the word. None is its own answer: several of these fields mean
    something different when the document does not declare them."""
    assert _preview.declared_flag(value) is expected


def test_a_declared_flag_unwraps_the_value_nested_under_its_own_name():
    assert _preview.declared_flag({"selfProvider": False}, key="selfProvider") is False
    assert _preview.declared_flag({"selfProvider": "true"}, key="selfProvider") is True
    assert _preview.declared_flag({"other": True}, key="selfProvider") is None


def test_a_condition_the_export_says_is_not_instanced_is_not_called_instanced(built):
    """``instanced="false"`` was reported as instanced: the string is truthy,
    and the page said the opposite of the document."""
    _members, graph = built
    page = page_for(graph, "symptom:SymptomDefinition-VMWARE-Fixture_CPU_high")
    assert "(instanced)" not in page
    import xml.etree.ElementTree as ET

    assert "(instanced)" in _preview._condition_words(
        ET.Element("Condition", {"type": "metric", "key": "k", "operator": ">",
                                 "value": "1", "instanced": "true"}))
    assert "(instanced)" not in _preview._condition_words(
        ET.Element("Condition", {"type": "metric", "key": "k", "operator": ">",
                                 "value": "1", "instanced": "false"}))


def test_a_group_the_export_says_is_not_auto_resolved_says_so(built):
    _members, graph = built
    page = page_for(graph, f"customgroup:{GROUP_NAME_3}")
    assert "fixed at import" in page
    assert "kept up to date automatically" not in page
    # And a group that declares it true still reads true.
    assert "kept up to date automatically" in page_for(graph, f"customgroup:{GROUP_NAME}")


def test_an_outbound_setting_disabled_as_a_string_reads_as_disabled(built):
    _members, graph = built
    page = page_for(graph, "outboundsetting:")
    facts = re.findall(r"<dt>enabled</dt><dd>([^<]*)</dd>", page)
    assert facts == ["no"], page[:0] or facts


def test_a_flag_the_export_does_not_declare_is_not_reported_either_way():
    """"not declared" is a third answer, and the fields that have it mean
    something different when absent."""
    assert _preview._flag_words(None, "yes", "no") == "not declared"
    assert _preview._flag_words("false", "yes", "no") == "no"
    assert _preview._flag_words(True, "yes", "no") == "yes"


def test_a_column_the_export_says_is_not_text_is_a_number(built):
    """``isStringAttribute="false"`` decides the cell, so a numeric column
    cannot be filled with sample text."""
    _members, graph = built
    root = _preview._xml_doc(_preview.raw_document(
        graph, node_for(graph, f"view:{VIEW_IDS[0]}")))
    columns = _preview.view_columns(root)
    by_label = {c.label: c for c in columns}
    assert by_label["CPU Usage"].is_string is False
    assert by_label["Cluster"].is_string is True
