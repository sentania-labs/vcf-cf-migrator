"""Build a hand-made, export-shaped zip for tests and CI.

Mirrors the layout of a real content export (the factory's wire-formats
reference): marker, configuration.json, usermappings.json, views.zip with
content.xml, dashboards/<owner> inner zip, dashboardsharings/<owner>,
supermetrics.json. On top, the members a UI export of the other types
carries: AlertContent.xml, CustomGroup.json, Notification Setting.json, a
reports zip, and one properties file that inspect must list as carried.

Made-up names, no instance data. Nothing here reads a real corpus.

Usage: python tests/fixtures/make_export_fixture.py OUT.zip [--version X.Y]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

OWNER = "b58a71ee-e909-5b40-a355-9e199e6f0f53"
MARKER = "1757800000000000000L.v1"

DASHBOARD_ID = "2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b"
VIEW_IDS = ("6e8310ed-1753-45a4-aacc-7f1025c03d11", "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d")
SM_IDS = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
REPORT_ID = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f"
RULE_ID = "5e9c97aa-a5b0-473e-b51f-a581b2535f59"
TEMPLATE_ID = "b97f2879-57ef-4317-880c-a1a0a1f3ecab"

EXPECTED_ITEMS = {
    ("dashboard", "[Fixture] Cluster Overview", DASHBOARD_ID),
    ("view", "[Fixture] Cluster List", VIEW_IDS[0]),
    ("view", "[Fixture] VM List", VIEW_IDS[1]),
    ("supermetric", "[Fixture] SM 1", SM_IDS[0]),
    ("supermetric", "[Fixture] SM 2", SM_IDS[1]),
    ("customgroup", "[Fixture] Prod Clusters", ""),
    ("symptom", "[Fixture] CPU high", "SymptomDefinition-VMWARE-Fixture_CPU_high"),
    ("alert", "[Fixture] Cluster CPU alert", "AlertDefinition-VMWARE-Fixture_Cluster_CPU"),
    ("recommendation", "Add hosts to the cluster", "Recommendation-df-VMWARE-Fixture_Add_hosts"),
    ("report", "[Fixture] Cluster Report", REPORT_ID),
    ("notificationrule", "[Fixture] Cluster rule", RULE_ID),
    ("notificationtemplate", "[Fixture] Cluster template", TEMPLATE_ID),
}
EXPECTED_CARRIED = {"resources/content.properties"}


def _views_xml() -> str:
    defs = "".join(
        f'<ViewDef id="{vid}"><Title>{title}</Title><Description>made up</Description>'
        f'<SubjectType adapterKind="VMWARE" resourceKind="ClusterComputeResource" type="self"/>'
        f"<Usage><Dashboard/></Usage><Controls/></ViewDef>"
        for vid, title in zip(VIEW_IDS, ("[Fixture] Cluster List", "[Fixture] VM List"))
    )
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Content><Views>{defs}</Views></Content>'


def _reports_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Content><Reports>'
        f'<ReportDef id="{REPORT_ID}"><isTenant>false</isTenant><Title>[Fixture] Cluster Report</Title>'
        f'<Description>made up</Description><Sections><Section><ContentType>View</ContentType>'
        f'<ContentKey>{VIEW_IDS[0]}</ContentKey></Section></Sections></ReportDef>'
        "</Reports></Content>"
    )


def _alert_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><alertContent>'
        '<AlertDefinitions><AlertDefinition adapterKind="VMWARE" id="AlertDefinition-VMWARE-Fixture_Cluster_CPU" '
        'name="[Fixture] Cluster CPU alert" resourceKind="ClusterComputeResource" type="16" subType="19">'
        '<State severity="critical"><SymptomSet ref="SymptomDefinition-VMWARE-Fixture_CPU_high" aggregation="all"/>'
        '<Recommendation priority="1" ref="Recommendation-df-VMWARE-Fixture_Add_hosts"/></State>'
        "</AlertDefinition></AlertDefinitions>"
        '<SymptomDefinitions><SymptomDefinition adapterKind="VMWARE" id="SymptomDefinition-VMWARE-Fixture_CPU_high" '
        'name="[Fixture] CPU high" resourceKind="ClusterComputeResource"/></SymptomDefinitions>'
        '<Recommendations><Recommendation key="Recommendation-df-VMWARE-Fixture_Add_hosts" '
        'description="Add hosts to the cluster"/></Recommendations>'
        "</alertContent>"
    )


def _dashboard_json() -> dict:
    return {
        "uuid": DASHBOARD_ID,
        "entries": {"resourceKind": [], "resource": []},
        "dashboards": [{
            "id": DASHBOARD_ID,
            "name": "[Fixture] Cluster Overview",
            "widgets": [{"type": "VIEW", "config": {"viewId": VIEW_IDS[0]}, "gridsterCoords": {}}],
            "widgetInteractions": [],
        }],
    }


def build_export_zip(version: str = "") -> bytes:
    """The fixture bytes. *version* goes into configuration.json when given;
    real exports carry none, so the default leaves it out."""
    views_inner = io.BytesIO()
    with zipfile.ZipFile(views_inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", _views_xml())
    reports_inner = io.BytesIO()
    with zipfile.ZipFile(reports_inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", _reports_xml())
    dash_inner = io.BytesIO()
    with zipfile.ZipFile(dash_inner, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("dashboard/dashboard.json", json.dumps(_dashboard_json()))
        z.writestr("dashboard/resources/resources.properties", "")

    sms = {
        SM_IDS[0]: {"name": "[Fixture] SM 1", "formula": "1", "description": "", "unitId": "", "resourceKinds": []},
        SM_IDS[1]: {"name": "[Fixture] SM 2", "formula": "2", "description": "", "unitId": "", "resourceKinds": []},
    }
    groups = {"customGroups": [{
        "name": "[Fixture] Prod Clusters", "description": "", "adapterKind": "Container",
        "resourceKind": "Environment", "autoResolveMembership": True, "started": True,
        "membershipDefinition": {"rules": []},
    }], "customGroupTypes": []}
    rules = {"NotificationRules": {
        "notificationRules": [{"NotificationRule": {
            "id": RULE_ID, "Name": "[Fixture] Cluster rule", "Description": "", "PluginType": "WebhookPlugin",
            "Disabled": "False", "RuleType": "GENERAL_RULE",
        }}],
        "notificationTemplateDataSet": [{"NotificationTemplateData": {
            "id": TEMPLATE_ID, "Name": "[Fixture] Cluster template", "pluginTypeId": "WebhookPlugin",
        }}],
        "ruleNameToTemplateNameMap": [],
    }}
    manifest = {"dashboards": 1, "views": 2, "superMetrics": 2, "type": "CUSTOM"}
    if version:
        manifest["version"] = version

    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MARKER, OWNER)
        z.writestr("configuration.json", json.dumps(manifest))
        z.writestr("views.zip", views_inner.getvalue())
        z.writestr("usermappings.json", json.dumps({OWNER: {"userName": "admin", "userId": OWNER}}))
        z.writestr(f"dashboards/{OWNER}", dash_inner.getvalue())
        z.writestr(f"dashboardsharings/{OWNER}", "[]")
        z.writestr("supermetrics.json", json.dumps(sms))
        z.writestr("AlertContent.xml", _alert_xml())
        z.writestr("CustomGroup.json", json.dumps(groups))
        z.writestr("Notification Setting.json", json.dumps(rules))
        z.writestr("Reports.zip", reports_inner.getvalue())
        z.writestr("resources/content.properties", "fixture=1\n")
    return outer.getvalue()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out", help="path of the zip to write")
    p.add_argument("--version", default="", help="version string to put in configuration.json (default: none)")
    args = p.parse_args(argv)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_export_zip(args.version))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
