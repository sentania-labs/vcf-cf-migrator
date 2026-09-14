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

OWNER = "aaaa1111-0000-4000-8000-00000000000a"
OWNER_2 = "bbbb2222-0000-4000-8000-00000000000b"  # shares the dashboard with OWNER
MARKER = "1757800000000000000L.v1"

DASHBOARD_ID = "2d7b8c1e-4f11-4c7a-9a55-0c1f2e3d4a5b"
VIEW_IDS = ("6e8310ed-1753-45a4-aacc-7f1025c03d11", "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d")
SM_IDS = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
REPORT_ID = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f"
RULE_ID = "5e9c97aa-a5b0-473e-b51f-a581b2535f59"
RULE_ID_2 = "7f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b"
TEMPLATE_ID = "b97f2879-57ef-4317-880c-a1a0a1f3ecab"
TEMPLATE_ID_2 = "c0a1b2c3-d4e5-4f60-8a7b-8c9d0e1f2a3b"

# A dashboard shared by two owners lists once per owner (a set collapses
# the pair, so EXPECTED_DASHBOARD_LISTINGS carries the count).
EXPECTED_DASHBOARD_LISTINGS = 2
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
    ("notificationrule", "[Fixture] Host rule", RULE_ID_2),
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


def _alertdefs_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><alertContent>'
        '<AlertDefinitions><AlertDefinition adapterKind="VMWARE" id="AlertDefinition-VMWARE-Fixture_Cluster_CPU" '
        'name="[Fixture] Cluster CPU alert" resourceKind="ClusterComputeResource" type="16" subType="19">'
        '<State severity="critical"><SymptomSet ref="SymptomDefinition-VMWARE-Fixture_CPU_high" aggregation="all"/>'
        '<Recommendation priority="1" ref="Recommendation-df-VMWARE-Fixture_Add_hosts"/></State>'
        "</AlertDefinition></AlertDefinitions></alertContent>"
    )


def _symptomdefs_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><alertContent>'
        '<SymptomDefinitions><SymptomDefinition adapterKind="VMWARE" id="SymptomDefinition-VMWARE-Fixture_CPU_high" '
        'name="[Fixture] CPU high" resourceKind="ClusterComputeResource"/></SymptomDefinitions></alertContent>'
    )


def _recommendationdefs_xml() -> str:
    # Real exports put the text in a Description child, not an attribute.
    return (
        '<?xml version="1.0" encoding="UTF-8"?><alertContent>'
        '<Recommendations><Recommendation key="Recommendation-df-VMWARE-Fixture_Add_hosts">'
        "<Description>Add hosts to the cluster</Description></Recommendation></Recommendations></alertContent>"
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


def build_export_zip(without=()) -> bytes:
    """The fixture bytes. *without* names members to leave out, so tests can
    mirror an export that carries fewer content types."""
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
    # 9.1.1 nesting: one entry whose NotificationRule key holds the list of
    # rules (8.x carries one dict per entry; the reader takes both).
    rules = {"NotificationRules": {
        "notificationRules": [{"NotificationRule": [
            {"id": RULE_ID, "Name": "[Fixture] Cluster rule", "Description": "", "PluginType": "WebhookPlugin",
             "PluginID": {"@pluginType": "WebhookPlugin", "@pluginName": "fixture"},
             "Disabled": "False", "RuleType": "GENERAL_RULE", "entry": []},
            {"id": RULE_ID_2, "Name": "[Fixture] Host rule", "Description": "", "PluginType": "StandardEmailPlugin",
             "PluginID": {"@pluginType": "StandardEmailPlugin", "@pluginName": "fixture"},
             "Disabled": "False", "RuleType": "GENERAL_RULE", "entry": []},
        ]}],
        "ruleNameToTemplateNameMap": [],
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
    outbound = {"serviceCredentials": [], "exportId": "fixture", "plugins": [{
        "pluginType": "StandardEmailPlugin",
        "pluginConfig": {"pluginName": "[Fixture] Mail relay", "enabled": True, "resIdent": []},
    }]}
    manifest = {"dashboards": 2, "views": 2, "superMetrics": 2, "customGroups": 1, "reports": 1,
                "symptomDefs": 1, "alertDefs": 1, "notificationRules": 2, "payloadTemplates": 2, "type": "CUSTOM",
                "dashboardsByOwner": [{"owner": OWNER, "count": 1}, {"owner": OWNER_2, "count": 1}]}
    policies = '<?xml version="1.0" encoding="UTF-8"?><PolicyContent><Policies/></PolicyContent>'

    members = [
        (MARKER, OWNER),
        ("configuration.json", json.dumps(manifest)),
        ("views.zip", views_inner.getvalue()),
        ("usermappings.json", json.dumps({OWNER: {"userName": "admin", "userId": OWNER},
                                          OWNER_2: {"userName": "operator", "userId": OWNER_2}})),
        (f"dashboards/{OWNER}", dash_inner.getvalue()),
        (f"dashboardsharings/{OWNER}", "[]"),
        # The same dashboard (same uuid) exported under a second owner.
        (f"dashboards/{OWNER_2}", dash_inner.getvalue()),
        (f"dashboardsharings/{OWNER_2}", "[]"),
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
