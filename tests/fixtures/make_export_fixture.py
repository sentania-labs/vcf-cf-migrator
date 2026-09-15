"""Build a hand-made, export-shaped zip for tests and CI.

Mirrors the member layout of the two corpus exports (8.18.7 and 9.0.2,
identical for every content type both carry): the ``<digits>L.v1`` marker,
configuration.json, usermappings.json, views.zip with content.xml,
dashboards/<owner> inner zip, dashboardsharings/<owner>, supermetrics.json,
symptomdefs.xml, alertdefs.xml, recommendationdefs.xml (each an alertContent
document), customgroups.json, notificationrules.json, payloadtemplates.json,
outboundsettings.json, reports.zip, plus one policies.xml that inspect must
list as carried. No member carries a product version; real exports do not.

Made-up names, no instance data. Nothing here reads a real corpus.

Usage: python tests/fixtures/make_export_fixture.py OUT.zip [--without MEMBER ...]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

# Invented people and invented secrets. Nothing here comes from any instance:
# the log's exclusion rules are a correctness requirement, so the committed
# fixture has to carry something of every excluded class for the suite to
# prove they are excluded.
PERSON_USER_NAME = "fixture-operator"
PERSON_DISPLAY_NAME = "Fixture Owner One"
PERSON_DISPLAY_NAME_2 = "Fixture Owner Two"
PERSON_MAIL = "fixture.admin@example.invalid"
PERSON_SERVICE_ACCOUNT = "fixture-service-account"
SECRET_CIPHER_TEXT = "ENC(invented-cipher-text-not-a-secret)"
SECRET_TOKEN = "invented-bearer-token-not-a-secret"

OWNER = "aaaa1111-0000-4000-8000-00000000000a"
OWNER_2 = "bbbb2222-0000-4000-8000-00000000000b"  # shares the dashboard with OWNER
MARKER = "1757800000000000000L.v1"
VIEWS_SIBLING = "resources/views.properties"
VIEWS_SIBLING_BODY = "#Views localization\nGROUP_hardware=Hardware\n"

DASHBOARD_ID = "2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b"
# Only OWNER_2 has this one, so a selection can cross two owner members.
DASHBOARD_ID_2 = "8e1c2d3f-5a6b-4c7d-9e8f-0a1b2c3d4e5f"
VIEW_IDS = ("6e8310ed-1753-45a4-aacc-7f1025c03d11", "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d")
# A view whose document is present and declares no columns.
EMPTY_VIEW_ID = "7b8c9d0e-1f2a-4b3c-8d4e-5f6a7b8c9d0e"
# A super metric whose document is present and whose formula is empty.
EMPTY_SM_ID = "44444444-4444-4444-8444-444444444444"
SM_IDS = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
          "33333333-3333-4333-8333-333333333333")
REPORT_ID = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f"
# Widget ids inside [Fixture] Cluster Overview: one drives another, and a
# third waits on a selection nothing provides.
WIDGET_PROVIDER = "4a5b6c7d-1111-4111-8111-0a1b2c3d4e5f"
WIDGET_RECEIVER = "4a5b6c7d-2222-4222-8222-0a1b2c3d4e5f"
WIDGET_ORPHAN = "4a5b6c7d-3333-4333-8333-0a1b2c3d4e5f"
# A selector whose own config is empty: VCF Operations keeps a resource
# list's column layout in states[].value, and 48 widgets in the corpus drive
# other widgets while carrying a config of {}. A selector is never "carries
# nothing", whatever its config looks like.
WIDGET_BARE_SELECTOR = "4a5b6c7d-4444-4444-8444-0a1b2c3d4e5f"
WIDGET_SELECTOR_FED = "4a5b6c7d-5555-4555-8555-0a1b2c3d4e5f"
# A widget with no config and a saved-state blob: configured, in a place this
# preview does not decode.
WIDGET_STATE_ONLY = "4a5b6c7d-6666-4666-8666-0a1b2c3d4e5f"
# Two selectors whose own renderer finds nothing to draw: a scoreboard with
# an empty metric block, and a view widget naming a view that declares no
# columns. Both drive another widget, so neither may be told it shows
# nothing: they are the dashboard's controls.
WIDGET_DRIVING_SCOREBOARD = "4a5b6c7d-7777-4777-8777-0a1b2c3d4e5f"
WIDGET_DRIVING_VIEW = "4a5b6c7d-8888-4888-8888-0a1b2c3d4e5f"
WIDGET_HEATMAP = "4a5b6c7d-9999-4999-8999-0a1b2c3d4e5f"
WIDGET_HEALTH = "4a5b6c7d-aaaa-4aaa-8aaa-0a1b2c3d4e5f"
# A widget whose whole state value is the empty-object marker: an ExtJS
# object with no fields, which is not a layout.
WIDGET_EMPTY_STATE = "4a5b6c7d-bbbb-4bbb-8bbb-0a1b2c3d4e5f"
# A dashboard with no widgets at all, a second alert whose state names no
# symptom, two more symptoms (one with no state, one with a state and no
# condition), a recommendation with no text, a report with no sections and a
# rule with no conditions: one document per "carries nothing" case the
# preview can report, so none of them is exercised by hand only.
EMPTY_DASHBOARD_ID = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b"
EMPTY_ALERT_ID = "AlertDefinition-VMWARE-Fixture_No_Symptoms"
STATELESS_ALERT_ID = "AlertDefinition-VMWARE-Fixture_No_State"
EMPTY_SYMPTOM_ID = "SymptomDefinition-VMWARE-Fixture_No_State"
CONDITIONLESS_SYMPTOM_ID = "SymptomDefinition-VMWARE-Fixture_No_Condition"
EMPTY_RECOMMENDATION_ID = "Recommendation-df-VMWARE-Fixture_No_Text"
EMPTY_REPORT_ID = "6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c"
EMPTY_RULE_ID = "8a9b0c1d-2e3f-4a5b-8c6d-7e8f9a0b1c2d"
# A chart view carrying no attributes at all.
EMPTY_CHART_VIEW_ID = "9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1e2f"
# A view id no document defines, for a widget that names a view the export
# does not carry.
ABSENT_VIEW_ID = "0d1e2f3a-4b5c-4d6e-8f7a-8b9c0d1e2f3a"
# A super metric uuid nothing in the fixture defines, so an edge to an object
# the export does not carry can be exercised. Real exports are full of these.
ABSENT_SM_ID = "deadbeef-0000-4000-8000-000000000001"
ABSENT_ALERT_ID = "AlertDefinition-VMWARE-NotInThisExport"
# A super metric named by a formula that nothing in the fixture defines: the
# by-name spelling has to report a miss the same way the uuid spelling does.
ABSENT_SM_NAME = "[Fixture] SM Nowhere"
# A custom group named by a membership rule that names no group here. Most
# ruleStringValues name ordinary resources, so this one must stay silent.
ABSENT_GROUP_NAME = "[Fixture] Other Clusters"
GROUP_NAME = "[Fixture] Prod Clusters"
GROUP_NAME_2 = "[Fixture] Web Tier"
GROUP_NAME_3 = "[Fixture] Edge Nodes"
# A custom group's policy lives in policies.xml, which this tool never
# carries, so a carried group always points at a policy that will be absent.
GROUP_POLICY_ID = "9f1e2d3c-4b5a-4968-8777-6a5b4c3d2e1f"
RULE_ID = "5e9c97aa-a5b0-473e-b51f-a581b2535f59"
RULE_ID_2 = "7f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b"
TEMPLATE_ID = "b97f2879-57ef-4317-880c-a1a0a1f3ecab"
TEMPLATE_ID_2 = "c0a1b2c3-d4e5-4f60-8a7b-8c9d0e1f2a3b"

# A dashboard shared by two owners lists once per owner (a set collapses
# the pair, so EXPECTED_DASHBOARD_LISTINGS carries the count): OWNER has one
# dashboard, OWNER_2 has the same one plus a second of its own.
EXPECTED_DASHBOARD_LISTINGS = 4
EXPECTED_ITEMS = {
    ("dashboard", "[Fixture] Cluster Overview", DASHBOARD_ID),
    ("dashboard", "[Fixture] VM Overview", DASHBOARD_ID_2),
    ("dashboard", "[Fixture] Empty Overview", EMPTY_DASHBOARD_ID),
    ("view", "[Fixture] Cluster List", VIEW_IDS[0]),
    ("view", "[Fixture] VM List", VIEW_IDS[1]),
    ("view", "[Fixture] Empty List", EMPTY_VIEW_ID),
    ("view", "[Fixture] Empty Chart", EMPTY_CHART_VIEW_ID),
    ("supermetric", "[Fixture] SM 1", SM_IDS[0]),
    ("supermetric", "[Fixture] SM 2", SM_IDS[1]),
    ("supermetric", "[Fixture] SM 3", SM_IDS[2]),
    ("supermetric", "[Fixture] SM Empty", EMPTY_SM_ID),
    ("customgroup", GROUP_NAME, ""),
    ("customgroup", GROUP_NAME_2, ""),
    ("customgroup", GROUP_NAME_3, ""),
    ("symptom", "[Fixture] CPU high", "SymptomDefinition-VMWARE-Fixture_CPU_high"),
    ("symptom", "[Fixture] Symptom with no state", EMPTY_SYMPTOM_ID),
    ("symptom", "[Fixture] Symptom with no condition", CONDITIONLESS_SYMPTOM_ID),
    ("alert", "[Fixture] Cluster CPU alert", "AlertDefinition-VMWARE-Fixture_Cluster_CPU"),
    ("alert", "[Fixture] Alert with no symptoms", EMPTY_ALERT_ID),
    ("alert", "[Fixture] Alert with no state", STATELESS_ALERT_ID),
    ("recommendation", "Add hosts to the cluster", "Recommendation-df-VMWARE-Fixture_Add_hosts"),
    ("recommendation", "(unnamed)", EMPTY_RECOMMENDATION_ID),
    ("report", "[Fixture] Cluster Report", REPORT_ID),
    ("report", "[Fixture] Empty Report", EMPTY_REPORT_ID),
    ("notificationrule", "[Fixture] Cluster rule", RULE_ID),
    ("notificationrule", "[Fixture] Host rule", RULE_ID_2),
    ("notificationrule", "[Fixture] Rule with no conditions", EMPTY_RULE_ID),
    ("notificationtemplate", "[Fixture] Cluster template", TEMPLATE_ID),
    ("notificationtemplate", "[Fixture] Host template", TEMPLATE_ID_2),
    ("outboundsetting", "[Fixture] Mail relay (StandardEmailPlugin)", ""),
}
EXPECTED_CARRIED = {"policies.xml"}
# Which member each optional kind comes from, for the drop-a-member tests.
MEMBER_FOR_KIND = {
    "report": "reports.zip",
    "customgroup": "customgroups.json",
    "symptom": "symptomdefs.xml",
    "alert": "alertdefs.xml",
    "recommendation": "recommendationdefs.xml",
    "notificationrule": "notificationrules.json",
    "notificationtemplate": "payloadtemplates.json",
    "outboundsetting": "outboundsettings.json",
}


def _view_controls(sm_id: str) -> str:
    """The two shapes an export writes a view's columns in.

    The realistic one first: an ``attributes-selector`` control holding an
    ``attributeInfos`` list, one Value block per column, each carrying the
    attribute key, the display name and the unit. That is what every corpus
    view carries and what the preview reads its column headers from.

    Then a bare Control carrying the same super metric key as a plain
    attributeKey property, the other spelling of the same reference, so the
    dependency walk keeps being exercised on both.
    """
    columns = (
        (f"Super Metric|sm_{sm_id}", "[Fixture] Cluster Score", "false", ""),
        ("cpu|usage_average", "CPU Usage", "false", "percent"),
        ("summary|parentCluster", "Cluster", "true", ""),
    )
    items = "".join(
        "<Item><Value>"
        f'<Property name="objectType" value="RESOURCE"/>'
        f'<Property name="attributeKey" value="{key}"/>'
        f'<Property name="isStringAttribute" value="{is_string}"/>'
        f'<Property name="preferredUnitId" value="{unit}"/>'
        f'<Property name="isProperty" value="{is_string}"/>'
        f'<Property name="displayName" value="{label}"/>'
        "</Value></Item>"
        for key, label, is_string, unit in columns)
    return ('<Controls>'
            '<Control id="attributes-selector_id_1" type="attributes-selector" visible="false">'
            f'<Property name="attributeInfos"><List>{items}</List></Property>'
            '</Control>'
            f'<Control><Property name="attributeKey" value="Super Metric|sm_{sm_id}"/></Control>'
            '</Controls>')


def _views_xml() -> str:
    # The first view points at a super metric this export carries, the second
    # at one it does not: the tree has to show both, and only the first can be
    # pulled into a selection. The second is also a non-list presentation, the
    # case the preview states rather than draws.
    controls = (_view_controls(SM_IDS[0]), _view_controls(ABSENT_SM_ID))
    presentations = ("list", "donut-chart")
    providers = ("list-view", "distribution-view")
    defs = "".join(
        f'<ViewDef id="{vid}"><Title>{title}</Title><Description>made up</Description>'
        f'<SubjectType adapterKind="VMWARE" resourceKind="ClusterComputeResource" type="self"/>'
        f"<Usage>dashboard</Usage>{control}"
        f'<DataProviders><DataProvider dataType="{provider}" id="dp-{vid}"/></DataProviders>'
        f'<Presentation type="{presentation}"/></ViewDef>'
        for vid, title, control, presentation, provider in zip(
            VIEW_IDS, ("[Fixture] Cluster List", "[Fixture] VM List"), controls,
            presentations, providers)
    )
    # A chart view carrying no attributes at all: nothing to chart.
    defs += (f'<ViewDef id="{EMPTY_CHART_VIEW_ID}"><Title>[Fixture] Empty Chart</Title>'
             '<Description>made up</Description>'
             '<SubjectType adapterKind="VMWARE" resourceKind="ClusterComputeResource" type="self"/>'
             '<Usage>dashboard</Usage>'
             '<DataProviders><DataProvider dataType="distribution-view" id="dp-chart"/></DataProviders>'
             '<Presentation type="donut-chart"/></ViewDef>')
    # A third view with no attributes selector at all: its document is here
    # and declares no columns, so it shows an empty table wherever it is used.
    defs += (f'<ViewDef id="{EMPTY_VIEW_ID}"><Title>[Fixture] Empty List</Title>'
             '<Description>made up</Description>'
             '<SubjectType adapterKind="VMWARE" resourceKind="ClusterComputeResource" type="self"/>'
             '<Usage>dashboard</Usage>'
             '<DataProviders><DataProvider dataType="list-view" id="dp-empty"/></DataProviders>'
             '<Presentation type="list"/></ViewDef>')
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Content><Views>{defs}</Views></Content>'


def _reports_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Content><Reports>'
        f'<ReportDef id="{REPORT_ID}"><isTenant>false</isTenant><Title>[Fixture] Cluster Report</Title>'
        f'<Description>made up</Description><Sections>'
        f'<Section><ContentType>View</ContentType><ContentKey>{VIEW_IDS[0]}</ContentKey></Section>'
        f'<Section><ContentType>Dashboard</ContentType><ContentKey>{DASHBOARD_ID}</ContentKey></Section>'
        f'</Sections></ReportDef>'
        # A report with no sections: a cover page and nothing else.
        f'<ReportDef id="{EMPTY_REPORT_ID}"><isTenant>false</isTenant>'
        '<Title>[Fixture] Empty Report</Title><Description/></ReportDef>'
        "</Reports></Content>"
    )


def _alertdefs_xml() -> str:
    # The second definition declares a state that names no symptom: an alert
    # that can never fire, which is a different emptiness from no state at all.
    return (
        '<?xml version="1.0" encoding="UTF-8"?><alertContent>'
        '<AlertDefinitions>'
        '<AlertDefinition adapterKind="VMWARE" id="AlertDefinition-VMWARE-Fixture_Cluster_CPU" '
        'name="[Fixture] Cluster CPU alert" resourceKind="ClusterComputeResource" type="16" subType="19">'
        '<State severity="critical"><SymptomSet ref="SymptomDefinition-VMWARE-Fixture_CPU_high" aggregation="all"/>'
        '<Recommendation priority="1" ref="Recommendation-df-VMWARE-Fixture_Add_hosts"/></State>'
        "</AlertDefinition>"
        f'<AlertDefinition adapterKind="VMWARE" id="{EMPTY_ALERT_ID}" '
        'name="[Fixture] Alert with no symptoms" resourceKind="ClusterComputeResource" '
        'type="16" subType="19"><State severity="warning">'
        '<Impact key="health" type="badge"/></State></AlertDefinition>'
        # No State at all, which is a different emptiness from a state that
        # names no symptom.
        f'<AlertDefinition adapterKind="VMWARE" id="{STATELESS_ALERT_ID}" '
        'name="[Fixture] Alert with no state" resourceKind="ClusterComputeResource" '
        'type="16" subType="19"/>'
        "</AlertDefinitions></alertContent>"
    )


def _symptomdefs_xml() -> str:
    # The threshold is on a super metric attribute, which is how a symptom
    # reaches one: <Condition key="Super Metric|sm_<uuid>" type="metric"/>.
    return (
        '<?xml version="1.0" encoding="UTF-8"?><alertContent>'
        '<SymptomDefinitions><SymptomDefinition adapterKind="VMWARE" id="SymptomDefinition-VMWARE-Fixture_CPU_high" '
        'name="[Fixture] CPU high" resourceKind="ClusterComputeResource">'
        f'<State severity="warning"><Condition key="Super Metric|sm_{SM_IDS[1]}" operator="&gt;" '
        # instanced="false" is the string "false", which is truthy: read as a
        # boolean it said the opposite of what the export says.
        'thresholdType="static" type="metric" value="0.0" valueType="numeric" '
        'instanced="false"/></State>'
        '</SymptomDefinition>'
        # No State element at all, and a State with no Condition: two
        # different ways a symptom can carry nothing.
        f'<SymptomDefinition adapterKind="VMWARE" id="{EMPTY_SYMPTOM_ID}" '
        'name="[Fixture] Symptom with no state" resourceKind="ClusterComputeResource"/>'
        f'<SymptomDefinition adapterKind="VMWARE" id="{CONDITIONLESS_SYMPTOM_ID}" '
        'name="[Fixture] Symptom with no condition" resourceKind="ClusterComputeResource">'
        '<State severity="warning"/></SymptomDefinition>'
        '</SymptomDefinitions></alertContent>'
    )


def _recommendationdefs_xml() -> str:
    # Real exports put the text in a Description child, not an attribute.
    return (
        '<?xml version="1.0" encoding="UTF-8"?><alertContent>'
        '<Recommendations><Recommendation key="Recommendation-df-VMWARE-Fixture_Add_hosts">'
        "<Description>Add hosts to the cluster</Description></Recommendation>"
        # A recommendation with no description: an operator acting on the
        # alert is told nothing.
        f'<Recommendation key="{EMPTY_RECOMMENDATION_ID}"/>'
        "</Recommendations></alertContent>"
    )


def _kind_metric(key: str, label: str, unit="") -> dict:
    """The ``metric`` block the scoreboard family writes: a list of resource
    kind metrics, each with its key, its display name and its unit."""
    return {"mode": "resourceKind", "resourceMetrics": [],
            "resourceKindMetrics": [{"metricKey": key, "metricName": label,
                                     "metricUnitId": unit,
                                     "resourceKindName": "Cluster Compute Resource"}]}


def _laid_out_widgets() -> list:
    """One widget of every type the preview lays out on purpose.

    The preview claims thirteen types; a fixture carrying five of them leaves
    the other eight renderers executed by nothing, which is how a render
    surface starts drawing the wrong thing and still ships green
    (``tests/test_preview.py`` asserts the set here covers
    ``preview.HANDLED_WIDGETS``, so a new renderer fails until a widget for it
    lands in this list).

    Every metric key here is an ordinary VCF Operations key rather than a
    ``Super Metric|sm_<uuid>`` one, and no resource scope names a group, so
    these widgets add no edge to the dependency graph: they exercise the
    drawing, not the walking, which the two widgets in
    ``_cluster_overview`` already cover.
    """
    return [
        # Driven by the resource list below: selfProvider false, and an
        # interaction naming it as the receiver.
        {"type": "MetricChart", "title": "[Fixture] CPU over time", "id": WIDGET_RECEIVER,
         "gridsterCoords": {"x": 1, "y": 7, "w": 6, "h": 5},
         # The nested shape every corpus widget carries.
         "config": {"title": "[Fixture] CPU over time",
                    "selfProvider": {"selfProvider": False},
                    "metric": _kind_metric("cpu|usage_average", "CPU|Usage", "percent")}},
        {"type": "SparklineChart", "title": "[Fixture] Latency sparkline",
         "gridsterCoords": {"x": 7, "y": 7, "w": 6, "h": 5},
         "config": {"title": "[Fixture] Latency sparkline",
                    "metric": _kind_metric("virtualDisk|totalReadLatency_average",
                                           "Virtual Disk|Read Latency", "msec")}},
        {"type": "ParetoAnalysis", "title": "[Fixture] Top consumers",
         "gridsterCoords": {"x": 1, "y": 13, "w": 4, "h": 6},
         "config": {"title": "[Fixture] Top consumers", "barsCount": 8,
                    "metric": {"metricKey": "mem|consumed_average",
                               "name": "Memory|Consumed"},
                    "metricName": "Memory consumed",
                    "metricUnit": {"metricUnitId": "gb", "metricUnitName": "GB"}}},
        {"type": "Heatmap", "title": "[Fixture] Cluster heat", "id": WIDGET_HEATMAP,
         "gridsterCoords": {"x": 5, "y": 13, "w": 4, "h": 6},
         "config": {"title": "[Fixture] Cluster heat", "mode": "all",
                    "configs": [{"colorBy": "cpu|usage_average",
                                 "sizeBy": "cpu|demandmhz"}]}},
        # The bare selector and the widget it drives: config {} on the
        # selector, and a state blob carrying its column layout.
        {"type": "ResourceList", "title": "[Fixture] Bare selector",
         "id": WIDGET_BARE_SELECTOR,
         "gridsterCoords": {"x": 1, "y": 49, "w": 6, "h": 4}, "config": {},
         "states": [{"key": "permResGrid_widget_bare",
                     "value": "o%3Acolumns%3Da%253Ao%25253Aid%25253Ds%2525253Ah1"}]},
        {"type": "PropertyList", "title": "[Fixture] Driven properties",
         "id": WIDGET_SELECTOR_FED,
         "gridsterCoords": {"x": 7, "y": 49, "w": 6, "h": 4},
         "config": {"title": "[Fixture] Driven properties",
                    "selfProvider": {"selfProvider": False},
                    "metric": _kind_metric("config|hardware|num_Cpu", "Hardware|vCPUs")}},
        # No config at all, a saved-state blob, and it drives nothing: the
        # widget is configured where this preview does not read.
        {"type": "AlertList", "title": "[Fixture] Layout kept in state",
         "id": WIDGET_STATE_ONLY,
         "gridsterCoords": {"x": 1, "y": 53, "w": 6, "h": 4}, "config": {},
         "states": [{"key": "permAlertGrid_widget_state_only",
                     "value": "o%3Acolumns%3Da%253Ao%25253Aid%25253Ds%2525253Acrit"}]},
        # Two charts naming no metric, one per branch, since a gate that
        # passes on a sibling sharing a code is not a gate.
        {"type": "ParetoAnalysis", "title": "[Fixture] Pareto with no metric",
         "gridsterCoords": {"x": 7, "y": 53, "w": 6, "h": 4},
         "config": {"title": "[Fixture] Pareto with no metric", "barsCount": 5,
                    "topOption": "top"}},
        {"type": "HealthChart", "title": "[Fixture] Health with no metric",
         "gridsterCoords": {"x": 1, "y": 57, "w": 6, "h": 4},
         "config": {"title": "[Fixture] Health with no metric", "mode": "all",
                    "chartHeight": 100}},
        # A second widget naming the same absent view as the one above: one
        # missing edge for the pair, not two.
        {"type": "View", "title": "[Fixture] Second widget on the absent view",
         "gridsterCoords": {"x": 7, "y": 57, "w": 6, "h": 4},
         "config": {"title": "[Fixture] Second widget on the absent view",
                    "viewDefinitionId": ABSENT_VIEW_ID}},
        # A selector that declares selfProvider true: it drives other widgets
        # and picks its own subject, which is what 72 of the corpus's 92
        # selectors do and what the caption used to deny.
        {"type": "Scoreboard", "title": "[Fixture] Driving scoreboard",
         "id": WIDGET_DRIVING_SCOREBOARD,
         "gridsterCoords": {"x": 1, "y": 61, "w": 6, "h": 4},
         "config": {"title": "[Fixture] Driving scoreboard",
                    "selfProvider": {"selfProvider": True},
                    "metric": {"mode": "resourceKind", "resourceKindMetrics": [],
                               "resourceMetrics": []}}},
        {"type": "View", "title": "[Fixture] Driving view",
         "id": WIDGET_DRIVING_VIEW,
         "gridsterCoords": {"x": 7, "y": 61, "w": 6, "h": 4},
         "config": {"title": "[Fixture] Driving view",
                    "viewDefinitionId": EMPTY_VIEW_ID}},
        # The whole state value is the empty-object marker, so this widget is
        # not configured anywhere: it carries nothing.
        {"type": "Heatmap", "title": "[Fixture] Empty state blob",
         "id": WIDGET_EMPTY_STATE,
         "gridsterCoords": {"x": 1, "y": 65, "w": 6, "h": 4}, "config": {},
         "states": [{"key": "permHeat_widget_empty", "value": "o%3A"}]},
        {"type": "PropertyList", "title": "[Fixture] Cluster properties",
         "gridsterCoords": {"x": 9, "y": 13, "w": 4, "h": 6},
         "config": {"title": "[Fixture] Cluster properties",
                    "metric": _kind_metric("configuration|dpmConfiginfo|enabled",
                                           "Cluster Configuration|DPM Enabled")}},
        {"type": "AlertList", "title": "[Fixture] Open alerts",
         "gridsterCoords": {"x": 1, "y": 19, "w": 6, "h": 5},
         "config": {"title": "[Fixture] Open alerts", "mode": "all",
                    # The one alert this export carries, so the widget's rows
                    # can be checked against a name the preview resolved.
                    "alertDefinitions": ["AlertDefinition-VMWARE-Fixture_Cluster_CPU"]}},
        # The dashboard's selector: it chooses no subject of its own
        # (selfProvider false) and drives the chart above. 27 widgets shaped
        # exactly like this were once called "will never show data".
        {"type": "ResourceList", "title": "[Fixture] Clusters", "id": WIDGET_PROVIDER,
         "gridsterCoords": {"x": 7, "y": 19, "w": 6, "h": 5},
         "config": {"title": "[Fixture] Clusters", "mode": "all",
                    "selfProvider": {"selfProvider": False},
                    "additionalColumns": [{"name": "Memory|Usage",
                                           "metricKey": "mem|usage_average"}]}},
        # selfProvider false and nothing feeds it: on the real dashboard this
        # widget is permanently blank, which is state 3, not a tool limit.
        {"type": "SparklineChart", "title": "[Fixture] Orphaned trend",
         "id": WIDGET_ORPHAN, "gridsterCoords": {"x": 7, "y": 25, "w": 6, "h": 5},
         # The flat shape, which is what the key's name implies.
         "config": {"title": "[Fixture] Orphaned trend", "selfProvider": False,
                    "metric": _kind_metric("mem|usage_average", "Memory|Usage", "percent")}},
        # State 3, the Skittles case: a widget whose config is an empty
        # object. It shows nothing on the dashboard either, and saying that
        # beats naming a type this preview does not draw.
        {"type": "Skittles", "title": "[Fixture] Unfinished widget",
         "gridsterCoords": {"x": 1, "y": 37, "w": 4, "h": 4}, "config": {}},
        # State 3 on a type the preview does draw: a scoreboard carrying a
        # title and nothing else.
        {"type": "Scoreboard", "title": "[Fixture] Empty scoreboard",
         "gridsterCoords": {"x": 5, "y": 37, "w": 4, "h": 4},
         "config": {"title": "[Fixture] Empty scoreboard"}},
        # One widget per remaining "carries nothing" case, so every branch of
        # the reason table has a document behind it.
        {"type": "View", "title": "[Fixture] View widget naming nothing",
         "gridsterCoords": {"x": 9, "y": 37, "w": 4, "h": 4},
         "config": {"title": "[Fixture] View widget naming nothing",
                    "viewDefinitionId": "", "selectFirstRow": True}},
        {"type": "View", "title": "[Fixture] View widget naming an absent view",
         "gridsterCoords": {"x": 1, "y": 41, "w": 4, "h": 4},
         "config": {"title": "[Fixture] View widget naming an absent view",
                    "viewDefinitionId": ABSENT_VIEW_ID}},
        {"type": "View", "title": "[Fixture] View widget on the empty list",
         "gridsterCoords": {"x": 5, "y": 41, "w": 4, "h": 4},
         "config": {"title": "[Fixture] View widget on the empty list",
                    "viewDefinitionId": EMPTY_VIEW_ID}},
        {"type": "MetricChart", "title": "[Fixture] Chart with no metric",
         "gridsterCoords": {"x": 9, "y": 41, "w": 4, "h": 4},
         "config": {"title": "[Fixture] Chart with no metric", "depth": 1,
                    "metric": {"mode": "resourceKind", "resourceKindMetrics": [],
                               "resourceMetrics": []}}},
        {"type": "Heatmap", "title": "[Fixture] Heatmap with no metric",
         "gridsterCoords": {"x": 1, "y": 45, "w": 4, "h": 4},
         "config": {"title": "[Fixture] Heatmap with no metric", "mode": "all",
                    "configs": []}},
        {"type": "TextDisplay", "title": "[Fixture] Text with no text",
         "gridsterCoords": {"x": 5, "y": 45, "w": 4, "h": 4},
         "config": {"title": "[Fixture] Text with no text", "viewModeHTML": "",
                    "editorData": ""}},
        {"type": "Section", "title": "",
         "gridsterCoords": {"x": 9, "y": 45, "w": 4, "h": 1},
         "config": {"visualTheme": "dark"}},
        {"type": "HealthChart", "title": "[Fixture] Health", "id": WIDGET_HEALTH,
         "gridsterCoords": {"x": 1, "y": 25, "w": 6, "h": 5},
         "config": {"title": "[Fixture] Health", "mode": "all",
                    "metricName": "Badge|Health", "metricKey": "badge|health",
                    "metricUnit": {"metricUnitId": -1,
                                   "metricUnitName": "Default Unit"}}},
        {"type": "Section", "title": "[Fixture] Second half",
         "gridsterCoords": {"x": 1, "y": 31, "w": 12, "h": 1},
         "config": {"title": "[Fixture] Second half"}},
        # Wider than the grid the dashboard declares (x 7 + w 8 needs 14
        # columns of 12), which is the shape one corpus dashboard has. The
        # preview widens the grid rather than squashing it into a sliver.
        {"type": "View", "title": "[Fixture] Overhanging list",
         "gridsterCoords": {"x": 7, "y": 33, "w": 8, "h": 5},
         "config": {"title": "[Fixture] Overhanging list",
                    "viewDefinitionId": VIEW_IDS[0]}},
    ]


def _cluster_overview() -> dict:
    """A dashboard that reaches a view and, separately, a super metric: real
    widgets address sm_<uuid> without going through a view at all, plus one
    widget of every type the preview lays out."""
    return {
        "id": DASHBOARD_ID,
        "name": "[Fixture] Cluster Overview",
        "gridsterMaxColumns": 12,
        "widgets": [
            {"type": "View", "config": {"viewDefinitionId": VIEW_IDS[0]}, "gridsterCoords": {}},
            # The list-shaped scope: no resourceName, no resource kind, just
            # the group's name. The same binding as the object shape one
            # widget up, written the other way an export writes it.
            {"type": "Scoreboard", "gridsterCoords": {},
             "config": {"metrics": [{"metricKey": f"Super Metric|sm_{SM_IDS[1]}"}],
                        "resource": [{"name": GROUP_NAME_2, "id": "resource:id:4_::_"}]}},
        ] + _laid_out_widgets(),
        # The shape every corpus export writes: a flat list of
        # provider/type/receiver, every id a widget in this same document.
        "widgetInteractions": [
            {"widgetIdProvider": WIDGET_PROVIDER, "type": "resourceId",
             "widgetIdReceiver": WIDGET_RECEIVER},
            {"widgetIdProvider": WIDGET_BARE_SELECTOR, "type": "resourceId",
             "widgetIdReceiver": WIDGET_SELECTOR_FED},
            {"widgetIdProvider": WIDGET_DRIVING_SCOREBOARD, "type": "resourceId",
             "widgetIdReceiver": WIDGET_HEATMAP},
            {"widgetIdProvider": WIDGET_DRIVING_VIEW, "type": "resourceId",
             "widgetIdReceiver": WIDGET_HEALTH},
        ],
    }


def _vm_overview() -> dict:
    # Three shapes of the same binding, one per widget: the object with a
    # Container resource kind, the object with no resource kind at all (which
    # is why the kind is a negative filter and not a requirement), and the
    # per-dashboard entryKeys list.
    return {
        "id": DASHBOARD_ID_2,
        "name": "[Fixture] VM Overview",
        "widgets": [
            {"type": "View", "gridsterCoords": {}, "tabId": "tab-one",
             "config": {"viewDefinitionId": VIEW_IDS[1],
                        "resource": {"resourceId": "resource:id:0_::_",
                                     "resourceName": GROUP_NAME,
                                     "resourceKindId": "002009ContainerEnvironment"}}},
            # selfProvider false on a dashboard that wires nothing at all:
            # its subject arrives from outside, the way it does for a
            # dashboard opened in an object's context. Not a fault.
            {"type": "ProblemAlertsList", "gridsterCoords": {}, "tabId": "tab-one",
             "config": {"selfProvider": {"selfProvider": False},
                        "resource": {"resourceId": "resource:id:1_::_",
                                     "resourceName": GROUP_NAME_3}}},
            # A widget type the preview does not lay out: it has to be named,
            # not drawn as something else.
            {"type": "Geo", "title": "[Fixture] Where things are",
             "gridsterCoords": {"x": 1, "y": 9, "w": 6, "h": 5}, "tabId": "tab-two",
             "config": {"title": "[Fixture] Where things are",
                        "locationFile": "fixture-locations.json"}},
            # Markup inside a document, so the preview can prove it never
            # injects one: a text widget's content is shown as text.
            {"type": "TextDisplay", "title": "[Fixture] Notes",
             "gridsterCoords": {"x": 7, "y": 9, "w": 6, "h": 5}, "tabId": "tab-two",
             "config": {"title": "[Fixture] Notes",
                        "viewModeHTML": "<p>Read the <b>runbook</b> first."
                                        "</p><script>alert(1)</script>"}},
        ],
        # Two tabs, one of which the document names and one it does not: 9.1.1
        # writes a tabId per widget, and no corpus dashboard has more than one
        # tab, so this is the only place the multi-tab heading ever runs.
        "tabs": [{"id": "tab-one", "name": "[Fixture] Overview tab"}],
        "entryKeys": {"uuid": DASHBOARD_ID_2, "resourceKind": [],
                      "resource": [{"resourceKindKey": "Function",
                                    "internalId": "resource:id:2_::_",
                                    "adapterKindKey": "Container",
                                    "identifiers": [], "name": GROUP_NAME_2}]},
        "widgetInteractions": [],
    }


def _dashboard_json(dashboards) -> dict:
    return {
        "uuid": DASHBOARD_ID,
        "entries": {"resourceKind": [], "resource": []},
        "dashboards": dashboards,
    }


def build_export_zip(without=()) -> bytes:
    """The fixture bytes. *without* names members to leave out, so tests can
    mirror an export that carries fewer content types."""
    views_inner = io.BytesIO()
    with zipfile.ZipFile(views_inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", _views_xml())
        # A sibling of content.xml inside views.zip. Every views.zip and
        # reports.zip in all five corpus exports holds content.xml alone, so
        # nothing in the corpus can catch a rebuild that drops the siblings;
        # this is what does.
        z.writestr(VIEWS_SIBLING, VIEWS_SIBLING_BODY)
    reports_inner = io.BytesIO()
    with zipfile.ZipFile(reports_inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", _reports_xml())
    def _dash_zip(dashboards) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("dashboard/dashboard.json", json.dumps(_dashboard_json(dashboards)))
            z.writestr("dashboard/resources/resources.properties", "")
        return buf.getvalue()

    dash_inner = _dash_zip([_cluster_overview()])
    # The second owner carries the same dashboard (same uuid) plus one of its
    # own, so a selection can cross two owner members.
    # A dashboard whose document carries no widgets at all.
    empty_dashboard = {"id": EMPTY_DASHBOARD_ID, "name": "[Fixture] Empty Overview",
                       "widgets": [], "widgetInteractions": []}
    dash_inner_2 = _dash_zip([_cluster_overview(), _vm_overview(), empty_dashboard])

    sms = {
        # SM 1 reaches SM 2 by uuid, the 9.x spelling. Its description quotes
        # SM 3's uuid as prose, which is not a reference and must not be read
        # as one: real exports say "Companion to X (UUID ...)".
        SM_IDS[0]: {"name": "[Fixture] SM 1",
                    "formula": f"${{this, metric=Super Metric|sm_{SM_IDS[1]}}} + 1",
                    "description": f"Companion to [Fixture] SM 3 (UUID {SM_IDS[2]}).",
                    "unitId": "", "resourceKinds": []},
        # SM 2 reaches SM 3 by name, the 8.x spelling.
        SM_IDS[1]: {"name": "[Fixture] SM 2",
                    "formula": '${this, metric=Super Metric|@supermetric:"[Fixture] SM 3"} * 2',
                    "description": "", "unitId": "", "resourceKinds": []},
        # SM 3 names one that does not exist, which must be reported missing.
        # An empty formula: the document is here and computes nothing.
        EMPTY_SM_ID: {"name": "[Fixture] SM Empty", "formula": "", "description": "",
                      "unitId": "", "resourceKinds": []},
        SM_IDS[2]: {"name": "[Fixture] SM 3",
                    "formula": f'${{this, metric=Super Metric|@supermetric:"{ABSENT_SM_NAME}"}}',
                    "description": "", "unitId": "", "resourceKinds": []},
    }
    groups = {"customGroups": [{
        "name": GROUP_NAME, "description": "", "adapterKind": "Container",
        "resourceKind": "Environment", "autoResolveMembership": True, "started": True,
        # The policy is always going to be absent on the target: policies.xml
        # is a member this tool does not understand and never carries.
        "policy": GROUP_POLICY_ID,
        "membershipDefinition": {"ruleGroups": [{
            "resourceKind": "VirtualMachine", "adapterKind": "VMWARE",
            "rules": [
                # A RelationshipRule names another group. This one names no
                # group in this export, the common case, and stays silent.
                {"ruleType": "RelationshipRule", "ruleRelationshipType": "DESCENDANT",
                 "ruleStringOperator": "EQUALS", "ruleStringValue": ABSENT_GROUP_NAME},
                # A ResourceNameRule names a resource, never a group, even
                # when its value happens to be a group's name.
                {"ruleType": "ResourceNameRule", "ruleStringOperator": "CONTAINS",
                 "ruleStringValue": GROUP_NAME_2},
            ],
        }]},
    }, {
        "name": GROUP_NAME_3, "description": "", "adapterKind": "Container",
        "resourceKind": "Function", "autoResolveMembership": False, "started": True,
        "membershipDefinition": {"ruleGroups": []},
    }, {
        "name": GROUP_NAME_2, "description": "", "adapterKind": "Container",
        "resourceKind": "Function", "autoResolveMembership": True, "started": True,
        "membershipDefinition": {"ruleGroups": [{
            "resourceKind": "VirtualMachine", "adapterKind": "VMWARE",
            "rules": [{"ruleType": "RelationshipRule", "ruleRelationshipType": "DESCENDANT",
                       "ruleStringOperator": "EQUALS", "ruleStringValue": GROUP_NAME}],
        }]},
    }], "customGroupTypes": []}
    # 9.1.1 nesting: one entry whose NotificationRule key holds the list of
    # rules (8.x carries one dict per entry; the reader takes both).
    rules = {"NotificationRules": {
        "notificationRules": [{"NotificationRule": [
            # Rule 1 fires on one alert this export carries and one it does
            # not, and names an endpoint the export does not carry either.
            {"id": RULE_ID, "Name": "[Fixture] Cluster rule", "Description": "", "PluginType": "WebhookPlugin",
             "PluginID": {"@pluginType": "WebhookPlugin", "@pluginName": "fixture"},
             "Disabled": "False", "RuleType": "GENERAL_RULE",
             "entry": [{"ConditionType": "ALERT_DEFINITION_ID",
                        "NotificationRuleAlertDefinitionCondition": {"AlertDefinitionIds": [
                            {"AlertDefinitionID": ["AlertDefinition-VMWARE-Fixture_Cluster_CPU",
                                                   ABSENT_ALERT_ID]}]}}]},
            # Rule 2 names the outbound plugin the export does carry.
            # A resource condition carries the same by-name scope a widget
            # does, under a different key.
            {"id": RULE_ID_2, "Name": "[Fixture] Host rule", "Description": "", "PluginType": "StandardEmailPlugin",
             "PluginID": {"@pluginType": "StandardEmailPlugin", "@pluginName": "[Fixture] Mail relay"},
             "Disabled": "False", "RuleType": "GENERAL_RULE",
             "entry": [{"ConditionType": "RESOURCE_AND_CHILD",
                        "NotificationRuleResourcesCondition": {"ResourceItems": [
                            {"NotificationRuleResourceItem": [
                                {"ResourceID": {"resourceName": GROUP_NAME,
                                                "adapterKind": "Container",
                                                "resourceKind": "Environment"}}]}]}}]},
            {"id": EMPTY_RULE_ID, "Name": "[Fixture] Rule with no conditions",
             "Description": "", "PluginType": "StandardEmailPlugin",
             "PluginID": {"@pluginType": "StandardEmailPlugin",
                          "@pluginName": "[Fixture] Mail relay"},
             "Disabled": "False", "RuleType": "GENERAL_RULE", "entry": []},
        ]}],
        # Two blocks on purpose: 9.x writes entry as a list, 8.x writes it as
        # a single object, and a select-all must reshape neither.
        "ruleNameToTemplateNameMap": [
            {"entry": [{"string": ["[Fixture] Cluster rule", "[Fixture] Cluster template"]}]},
            {"entry": {"string": ["[Fixture] Host rule", "[Fixture] Host template"]}},
        ],
    }}
    # 9.x list nesting: one entry whose NotificationTemplateData key holds
    # the list of templates (the one-dict-per-entry form is also read).
    # 8.x carries ids as dicts; the listing must show the bare uuid.
    templates = {"NotificationTemplate": {"notificationTemplateData": [{
        "@class": "NotificationTemplateData",
        "NotificationTemplateData": [
            {"id": {"@ObjectType": "NOTIFICATION_TEMPLATE", "@UUID": TEMPLATE_ID},
             "Name": "[Fixture] Cluster template", "pluginTypeId": "WebhookPlugin"},
            {"id": TEMPLATE_ID_2, "Name": "[Fixture] Host template", "pluginTypeId": "StandardEmailPlugin"},
        ],
    }]}}
    # An invented cipher text and an invented token, so the committed tier can
    # prove the log excludes an export's encrypted values as well as its
    # people. Neither is a real secret and neither decrypts to anything.
    outbound = {"serviceCredentials": [
        {"id": "c0ffee00-0000-4000-8000-00000000c0de",
         "userName": PERSON_SERVICE_ACCOUNT, "password": SECRET_CIPHER_TEXT}],
        "exportId": "fixture", "plugins": [{
        "pluginType": "StandardEmailPlugin",
        # "false" as a string: JSON writes real booleans today, and nothing
        # stops an export writing the word.
        "pluginConfig": {"pluginName": "[Fixture] Mail relay", "enabled": "false",
                         "authToken": SECRET_TOKEN, "resIdent": []},
    }]}
    manifest = {"dashboards": 4, "views": 4, "superMetrics": 4, "customGroups": 3, "reports": 1,
                "symptomDefs": 1, "alertDefs": 1, "notificationRules": 2, "payloadTemplates": 2, "type": "CUSTOM",
                "dashboardsByOwner": [{"owner": OWNER, "count": 1}, {"owner": OWNER_2, "count": 3}]}
    policies = '<?xml version="1.0" encoding="UTF-8"?><PolicyContent><Policies/></PolicyContent>'

    members = [
        (MARKER, OWNER),
        ("configuration.json", json.dumps(manifest)),
        ("views.zip", views_inner.getvalue()),
        # Real exports write {"sources": [], "users": [{"userId": ...}, ...]}.
        # Every person value here is invented. They exist so the log's
        # exclusion rules have something to exclude in the committed tier:
        # a user name, a display name and a mail address, none of them from
        # any real instance.
        ("usermappings.json", json.dumps({"sources": [], "users": [
            {"userName": "admin", "userId": OWNER, "displayName": PERSON_DISPLAY_NAME,
             "emailAddress": PERSON_MAIL},
            {"userName": PERSON_USER_NAME, "userId": OWNER_2,
             "displayName": PERSON_DISPLAY_NAME_2}]})),
        (f"dashboards/{OWNER}", dash_inner),
        (f"dashboardsharings/{OWNER}",
         json.dumps([{"groupName": "Everyone", "sourceType": "LOCAL",
                      "dashboards": [{"dashboardId": DASHBOARD_ID, "edit": True}]}])),
        # The same dashboard (same uuid) exported under a second owner.
        (f"dashboards/{OWNER_2}", dash_inner_2),
        (f"dashboardsharings/{OWNER_2}",
         json.dumps([{"groupName": "Everyone", "sourceType": "LOCAL",
                      "dashboards": [{"dashboardId": DASHBOARD_ID_2, "edit": True}]}])),
        ("supermetrics.json", json.dumps(sms)),
        ("symptomdefs.xml", _symptomdefs_xml()),
        ("alertdefs.xml", _alertdefs_xml()),
        ("recommendationdefs.xml", _recommendationdefs_xml()),
        ("customgroups.json", json.dumps(groups)),
        ("notificationrules.json", json.dumps(rules)),
        ("payloadtemplates.json", json.dumps(templates)),
        ("outboundsettings.json", json.dumps(outbound)),
        ("reports.zip", reports_inner.getvalue()),
        ("policies.xml", policies),
    ]
    skip = set(without)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            if name in skip:
                continue
            z.writestr(name, data)
    return outer.getvalue()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out", help="path of the zip to write")
    p.add_argument("--without", nargs="*", default=[], metavar="MEMBER", help="members to leave out")
    args = p.parse_args(argv)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_export_zip(args.without))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
