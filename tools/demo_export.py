"""Invented exports for walking the app: one rich, four that go wrong.

Every name here is made up. The rich export holds one of each situation the
screens have to explain: two copies of a dashboard under two owners, a
dashboard that never finished loading (with and without a reason), one that
comes with VCF Operations, a widget whose view was deleted, a super metric
that needs one the export does not hold, a rule that sends through an
outbound setting the export does not hold, a group with a policy of its own,
and a dashboard shared with a company-directory group.

    python tools/demo_export.py OUTPUT_DIRECTORY

writes ``ops-prod-content-export.zip`` and the four bad files beside it.
``tools/smoke_ux.py`` drives the real window over the same files.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

ADMIN = 'aaaa0000-0000-4000-8000-000000000001'
DANA = 'bbbb0000-0000-4000-8000-000000000002'
MARKER = '1757800000000000000L.v1'


def _id(kind: str, number: int) -> str:
    return f'{kind}{number:07d}-0000-4000-8000-{number:012d}'


D = {name: _id('d', number) for number, name in enumerate(
    ['capacity', 'datastore', 'hardware', 'kubernetes', 'noc', 'sql', 'rightsizing', 'vsan'], 1)}
V = {name: _id('e', number) for number, name in enumerate(['cluster', 'growth', 'hosts', 'oversized'], 1)}
V_GONE = _id('e', 99)
S = {name: _id('5', number) for number, name in enumerate(['headroom', 'overcommit', 'growth', 'perhost'], 1)}
RULES = {name: _id('c', number) for number, name in enumerate(['storage', 'capacity'], 1)}
TEMPLATES = {name: _id('7', number) for number, name in enumerate(['webhook', 'email'], 1)}
REPORT = _id('f', 1)
POLICY = _id('9', 1)

NAMES = {
    'capacity': 'Capacity Overview', 'datastore': 'Datastore Health', 'hardware': 'Host Hardware',
    'kubernetes': 'Kubernetes Clusters', 'noc': 'NOC Wallboard', 'sql': 'SQL Server Estate',
    'rightsizing': 'VM Rightsizing', 'vsan': 'vSAN Operations Overview',
}


def _view(key: str, title: str, columns) -> str:
    items = ''.join(
        '<Item><Value><Property name="objectType" value="RESOURCE"/>'
        f'<Property name="attributeKey" value="{attribute}"/>'
        f'<Property name="isStringAttribute" value="{"true" if text else "false"}"/>'
        '<Property name="preferredUnitId" value=""/>'
        f'<Property name="isProperty" value="{"true" if text else "false"}"/>'
        f'<Property name="displayName" value="{label}"/></Value></Item>'
        for attribute, label, text in columns)
    return (f'<ViewDef id="{V[key]}"><Title>{title}</Title><Description>Invented for the demo.</Description>'
            '<SubjectType adapterKind="VMWARE" resourceKind="ClusterComputeResource" type="self"/>'
            '<Usage>dashboard</Usage><Controls><Control id="attributes-selector_id_1" '
            'type="attributes-selector" visible="false"><Property name="attributeInfos"><List>'
            f'{items}</List></Property></Control></Controls><DataProviders>'
            f'<DataProvider dataType="list-view" id="dp-{V[key]}"/></DataProviders>'
            '<Presentation type="list"/></ViewDef>')


def _views_xml() -> str:
    sm = lambda name: f'Super Metric|sm_{S[name]}'  # noqa: E731
    views = [
        _view('cluster', 'Cluster Capacity Summary',
              [('summary|name', 'Cluster', True), (sm('headroom'), 'CPU Headroom', False),
               (sm('overcommit'), 'Memory Overcommit', False), ('cpu|usage_average', 'CPU Usage', False)]),
        _view('growth', 'Datastore Growth Trend',
              [('summary|name', 'Datastore', True), ('diskspace|used', 'Used', False),
               (sm('growth'), 'Growth per Day', False)]),
        _view('hosts', 'Host Hardware Inventory',
              [('summary|name', 'Host', True), ('hardware|model', 'Model', True),
               ('cpu|corecount', 'Cores', False), (sm('perhost'), 'VMs per Host', False)]),
        _view('oversized', 'Oversized Virtual Machines',
              [('summary|name', 'Virtual machine', True), ('summary|parentCluster', 'Cluster', True),
               ('config|numCpu', 'vCPUs', False), ('cpu|reclaimable', 'Reclaimable vCPUs', False)]),
    ]
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Content><Views>'
            + ''.join(views) + '</Views></Content>')


def _box(x, y, w, h):
    return {'x': x, 'y': y, 'w': w, 'h': h}


def _view_widget(title, view_id, coords):
    return {'id': _id('a', abs(hash(title)) % 9000000), 'type': 'View', 'title': title,
            'gridsterCoords': coords, 'config': {'title': title, 'viewDefinitionId': view_id}}


def _score(title, metrics, coords, resource=None):
    config = {'title': title, 'metrics': [{'metricKey': f'Super Metric|sm_{S[m]}', 'label': label}
                                           for m, label in metrics]}
    if resource:
        config['resource'] = {'resourceId': resource}
    return {'id': _id('a', abs(hash(title)) % 9000000), 'type': 'Scoreboard', 'title': title,
            'gridsterCoords': coords, 'config': config}


VC = {'adapterKind': 'adapterKind:id:0_::_'}


def _dashboards():
    """owner -> (dashboards, entries table)."""
    vcenter = [{'internalId': 'adapterKind:id:0_::_', 'adapterKindKey': 'VMWARE', 'name': 'VMWARE'}]
    production = {'internalId': 'resource:id:0_::_', 'adapterKindKey': 'Container',
                  'resourceKindKey': 'Environment', 'identifiers': [], 'name': 'Production Clusters'}
    tier1 = {'internalId': 'resource:id:1_::_', 'adapterKindKey': 'Container',
             'resourceKindKey': 'Environment', 'identifiers': [], 'name': 'Tier 1 Applications'}

    def capacity(changed):
        headline = _score('Headroom at a glance', [('overcommit', 'Memory overcommit')], _box(1, 1, 12, 3),
                          'resource:id:0_::_')
        headline['config'].update(VC)
        table = _view_widget('Cluster Capacity Summary', V['cluster'], _box(1, 4, 7, 6))
        trend = {'id': _id('a', 11), 'type': 'MetricChart', 'title': 'CPU headroom trend',
                 'gridsterCoords': _box(8, 4, 5, 6),
                 'config': {'title': 'CPU headroom trend',
                            'metrics': [{'metricKey': f'Super Metric|sm_{S["headroom"]}',
                                         'label': 'CPU headroom'}]}}
        return {'id': D['capacity'], 'name': NAMES['capacity'], 'gridsterMaxColumns': 12,
                'lastModified': changed, 'widgets': [headline, table, trend],
                'dashboardNavigations': {headline['id']: [{'id': D['rightsizing'], 'widgets': []}]},
                'widgetInteractions': []}

    datastore = {'id': D['datastore'], 'name': NAMES['datastore'], 'gridsterMaxColumns': 12,
                 'widgets': [dict(_view_widget('Datastore Growth Trend', V['growth'], _box(1, 1, 6, 6)), **{}),
                             _view_widget('Latency outliers', V_GONE, _box(7, 1, 6, 6))],
                 'widgetInteractions': []}
    datastore['widgets'][0]['config'].update(VC)
    hardware = {'id': D['hardware'], 'name': NAMES['hardware'], 'gridsterMaxColumns': 12,
                'widgets': [_view_widget('Host Hardware Inventory', V['hosts'], _box(1, 1, 12, 6))],
                'widgetInteractions': []}
    hardware['widgets'][0]['config'].update(VC)
    kubernetes = {'id': D['kubernetes'], 'name': NAMES['kubernetes'], 'importComplete': False,
                  'importAttempts': 0, 'entryKeys': {},
                  'widgets': [{'id': _id('a', 21), 'type': 'View', 'title': 'Clusters', 'config': {}}],
                  'widgetInteractions': []}
    sql = {'id': D['sql'], 'name': NAMES['sql'], 'importComplete': False, 'importAttempts': 5,
           'entryKeys': {'adapterKind': [{'adapterKindKey': 'Microsoft SQL Server'}]},
           'widgets': [{'id': _id('a', 22), 'type': 'View', 'title': 'Instances', 'config': {}}],
           'widgetInteractions': []}
    rightsizing = {'id': D['rightsizing'], 'name': NAMES['rightsizing'], 'gridsterMaxColumns': 12,
                   'widgets': [_view_widget('Oversized Virtual Machines', V['oversized'], _box(1, 1, 8, 6)),
                               {'id': _id('a', 31), 'type': 'TextDisplay', 'title': 'Scope',
                                'gridsterCoords': _box(9, 1, 4, 6),
                                'config': {'title': 'Scope', 'resource': {'resourceId': 'resource:id:1_::_'},
                                           'viewModeHTML': 'Tier 1 applications only.'}}],
                   'widgetInteractions': []}
    rightsizing['widgets'][0]['config'].update(VC)
    vsan = {'id': D['vsan'], 'name': NAMES['vsan'], 'adapterName': 'VirtualAndPhysicalSANAdapter',
            'gridsterMaxColumns': 12,
            'widgets': [{'id': _id('a', 41), 'type': 'TextDisplay', 'title': 'About',
                         'gridsterCoords': _box(1, 1, 12, 3),
                         'config': {'title': 'About', 'viewModeHTML': 'Capacity and health of vSAN.'}}],
            'widgetInteractions': []}
    noc_score = _score('Cluster headroom', [('headroom', 'CPU headroom')], _box(1, 1, 5, 4),
                       'resource:id:0_::_')
    noc_score['config'].update(VC)
    noc = {'id': D['noc'], 'name': NAMES['noc'], 'gridsterMaxColumns': 12,
           'widgets': [noc_score,
                       {'id': _id('a', 51), 'type': 'ProblemAlertsList', 'title': 'Open capacity alerts',
                        'gridsterCoords': _box(6, 1, 7, 4), 'config': {'title': 'Open capacity alerts'}}],
           'widgetInteractions': []}
    return {
        ADMIN: ([capacity('2026-11-03T09:00:00Z'), datastore, hardware, kubernetes, sql, rightsizing, vsan],
                {'resourceKind': [], 'adapterKind': vcenter, 'resource': [production, tier1]}),
        DANA: ([capacity('2026-08-14T09:00:00Z'), noc],
               {'resourceKind': [], 'adapterKind': vcenter, 'resource': [production]}),
    }


def _dash_zip(dashboards, entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as inner:
        inner.writestr('dashboard/dashboard.json', json.dumps(
            {'uuid': dashboards[0]['id'], 'entries': entries, 'dashboards': dashboards}))
        inner.writestr('dashboard/resources/resources.properties', '')
    return buffer.getvalue()


def _inner(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as inner:
        inner.writestr(name, text)
    return buffer.getvalue()


def build_export(with_dashboards: bool = True) -> bytes:
    """The rich export. Without its dashboards it is the "missing its
    dashboards" file: the contents list still says nine."""
    supermetrics = {
        S['headroom']: {'name': 'Cluster CPU Headroom %', 'formula': '100 - avg(${adaptertype=VMWARE, '
                        'objecttype=HostSystem, attribute=cpu|usage_average, depth=1})',
                        'description': '', 'unitId': '',
                        'resourceKinds': [{'adapterKindKey': 'VMWARE', 'resourceKindKey': 'ClusterComputeResource'}]},
        S['overcommit']: {'name': 'Cluster Memory Overcommit Ratio',
                          'formula': f'${{this, metric=Super Metric|sm_{S["headroom"]}}} / 100',
                          'description': '', 'unitId': '', 'resourceKinds': []},
        S['growth']: {'name': 'Datastore Growth per Day',
                      'formula': '${this, metric=Super Metric|@supermetric:"Datastore Daily Delta"} * 1',
                      'description': '', 'unitId': '', 'resourceKinds': []},
        S['perhost']: {'name': 'VMs per Host', 'formula': 'count(${adaptertype=VMWARE, '
                       'objecttype=VirtualMachine, attribute=summary|running, depth=1})',
                       'description': '', 'unitId': '', 'resourceKinds': []},
    }
    groups = {'customGroups': [
        {'name': 'Production Clusters', 'description': '', 'adapterKind': 'Container',
         'resourceKind': 'Environment', 'autoResolveMembership': True, 'started': True, 'policy': POLICY,
         'membershipDefinition': {'ruleGroups': []}},
        {'name': 'Tier 1 Applications', 'description': '', 'adapterKind': 'Container',
         'resourceKind': 'Environment', 'autoResolveMembership': True, 'started': True,
         'membershipDefinition': {'ruleGroups': [{
             'resourceKind': 'VirtualMachine', 'adapterKind': 'VMWARE',
             'rules': [{'ruleType': 'RelationshipRule', 'ruleRelationshipType': 'DESCENDANT',
                        'ruleStringOperator': 'EQUALS', 'ruleStringValue': 'Production Clusters'}]}]}},
    ], 'customGroupTypes': []}
    symptoms = ('<?xml version="1.0" encoding="UTF-8"?><alertContent><SymptomDefinitions>'
                '<SymptomDefinition adapterKind="VMWARE" id="SymptomDefinition-demo-cpu-headroom" '
                'name="Cluster CPU headroom below 15%" resourceKind="ClusterComputeResource">'
                f'<State severity="warning"><Condition key="Super Metric|sm_{S["headroom"]}" operator="&lt;" '
                'thresholdType="static" type="metric" value="15.0" valueType="numeric" instanced="false"/>'
                '</State></SymptomDefinition>'
                '<SymptomDefinition adapterKind="VMWARE" id="SymptomDefinition-demo-datastore-growth" '
                'name="Datastore growth above 50 GB per day" resourceKind="Datastore">'
                f'<State severity="warning"><Condition key="Super Metric|sm_{S["growth"]}" operator="&gt;" '
                'thresholdType="static" type="metric" value="50.0" valueType="numeric" instanced="false"/>'
                '</State></SymptomDefinition></SymptomDefinitions></alertContent>')
    alerts = ('<?xml version="1.0" encoding="UTF-8"?><alertContent><AlertDefinitions>'
              '<AlertDefinition adapterKind="VMWARE" id="AlertDefinition-demo-cpu-headroom" '
              'name="Cluster is running out of CPU headroom" resourceKind="ClusterComputeResource" '
              'type="16" subType="19"><State severity="critical">'
              '<SymptomSet ref="SymptomDefinition-demo-cpu-headroom" aggregation="all"/>'
              '<Recommendation priority="1" ref="Recommendation-demo-add-hosts"/></State></AlertDefinition>'
              '<AlertDefinition adapterKind="VMWARE" id="AlertDefinition-demo-datastore-growth" '
              'name="Datastore is filling faster than planned" resourceKind="Datastore" type="16" '
              'subType="19"><State severity="warning">'
              '<SymptomSet ref="SymptomDefinition-demo-datastore-growth" aggregation="all"/></State>'
              '</AlertDefinition></AlertDefinitions></alertContent>')
    recommendations = ('<?xml version="1.0" encoding="UTF-8"?><alertContent><Recommendations>'
                       '<Recommendation key="Recommendation-demo-add-hosts"><Description>Add hosts to the '
                       'cluster or move workloads</Description></Recommendation></Recommendations>'
                       '</alertContent>')
    reports = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Content><Reports>'
               f'<ReportDef id="{REPORT}"><isTenant>false</isTenant><Title>Monthly Capacity Report</Title>'
               '<Description>Invented for the demo.</Description><Sections>'
               f'<Section><ContentType>View</ContentType><ContentKey>{V["cluster"]}</ContentKey></Section>'
               f'<Section><ContentType>Dashboard</ContentType><ContentKey>{D["rightsizing"]}</ContentKey></Section>'
               f'<Section><ContentType>Dashboard</ContentType><ContentKey>{D["vsan"]}</ContentKey></Section>'
               '</Sections></ReportDef></Reports></Content>')
    rules = {'NotificationRules': {
        'notificationRules': [{'NotificationRule': [
            {'id': RULES['storage'], 'Name': 'Email the storage team', 'Description': '',
             'PluginType': 'StandardEmailPlugin',
             'PluginID': {'@pluginType': 'StandardEmailPlugin', '@pluginName': 'Corporate mail relay'},
             'Disabled': 'False', 'RuleType': 'GENERAL_RULE',
             'entry': [{'ConditionType': 'ALERT_DEFINITION_ID',
                        'NotificationRuleAlertDefinitionCondition': {'AlertDefinitionIds': [
                            {'AlertDefinitionID': ['AlertDefinition-demo-datastore-growth']}]}}]},
            {'id': RULES['capacity'], 'Name': 'Page the capacity team', 'Description': '',
             'PluginType': 'WebhookPlugin',
             'PluginID': {'@pluginType': 'WebhookPlugin', '@pluginName': 'On-call webhook'},
             'Disabled': 'False', 'RuleType': 'GENERAL_RULE',
             'entry': [{'ConditionType': 'ALERT_DEFINITION_ID',
                        'NotificationRuleAlertDefinitionCondition': {'AlertDefinitionIds': [
                            {'AlertDefinitionID': ['AlertDefinition-demo-cpu-headroom']}]}}]},
        ]}],
        'ruleNameToTemplateNameMap': [
            {'entry': [{'string': ['Email the storage team', 'Storage team email']},
                       {'string': ['Page the capacity team', 'Capacity webhook message']}]}],
    }}
    templates = {'NotificationTemplate': {'notificationTemplateData': [{
        '@class': 'NotificationTemplateData',
        'NotificationTemplateData': [
            {'id': TEMPLATES['webhook'], 'Name': 'Capacity webhook message', 'pluginTypeId': 'WebhookPlugin'},
            {'id': TEMPLATES['email'], 'Name': 'Storage team email', 'pluginTypeId': 'StandardEmailPlugin'}]}]}}
    outbound = {'serviceCredentials': [], 'exportId': 'demo', 'plugins': [{
        'pluginType': 'StandardEmailPlugin',
        'pluginConfig': {'pluginName': 'Corporate mail relay', 'enabled': 'true', 'resIdent': []}}]}
    policies = ('<?xml version="1.0" encoding="UTF-8"?><PolicyContent><Policies>'
                f'<Policy id="{POLICY}" name="Production"><SuperMetrics>'
                f'<SuperMetric id="{S["headroom"]}" enabled="true"/></SuperMetrics></Policy>'
                '</Policies></PolicyContent>')
    manifest = {'dashboards': 9, 'views': 4, 'superMetrics': 4, 'customGroups': 2, 'reports': 1,
                'symptomDefs': 2, 'alertDefs': 2, 'recommendations': 1, 'notificationRules': 2,
                'payloadTemplates': 2, 'outboundSettings': 1, 'type': 'CUSTOM',
                'dashboardsByOwner': [{'owner': ADMIN, 'count': 7}, {'owner': DANA, 'count': 2}]}
    members = [
        (MARKER, ADMIN),
        ('configuration.json', json.dumps(manifest)),
        ('views.zip', _inner('content.xml', _views_xml())),
        ('usermappings.json', json.dumps({
            'sources': [{'id': 'src-1', 'name': 'corp.example', 'sourceType': 'ACTIVE_DIRECTORY'}],
            'users': [{'userName': 'admin', 'userId': ADMIN},
                      {'userName': 'dana.reyes', 'userId': DANA, 'userSourceId': 'src-1'}]})),
    ]
    if with_dashboards:
        dashboards = _dashboards()
        members += [
            (f'dashboards/{ADMIN}', _dash_zip(*dashboards[ADMIN])),
            (f'dashboardsharings/{ADMIN}', json.dumps([
                {'groupName': 'Everyone', 'sourceType': 'LOCAL',
                 'dashboards': [{'dashboardId': D[name], 'edit': False}
                                for name in ('capacity', 'hardware', 'vsan')]},
                {'groupName': 'Platform Engineering', 'sourceType': 'ACTIVE_DIRECTORY',
                 'dashboards': [{'dashboardId': D['rightsizing'], 'edit': True}]}])),
            (f'dashboards/{DANA}', _dash_zip(*dashboards[DANA])),
            (f'dashboardsharings/{DANA}', json.dumps([
                {'groupName': 'Everyone', 'sourceType': 'LOCAL',
                 'dashboards': [{'dashboardId': D['noc'], 'edit': False}]}])),
        ]
    members += [
        ('supermetrics.json', json.dumps(supermetrics)),
        ('symptomdefs.xml', symptoms), ('alertdefs.xml', alerts),
        ('recommendationdefs.xml', recommendations),
        ('customgroups.json', json.dumps(groups)),
        ('notificationrules.json', json.dumps(rules)),
        ('payloadtemplates.json', json.dumps(templates)),
        ('outboundsettings.json', json.dumps(outbound)),
        ('reports.zip', _inner('content.xml', reports)),
        ('policies.xml', policies),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for position, (name, data) in enumerate(members):
            if with_dashboards and name == f'dashboards/{ADMIN}':
                archive.writestr(zipfile.ZipInfo('dashboards/'), b'')
                archive.writestr(zipfile.ZipInfo('dashboardsharings/'), b'')
            archive.writestr(name, data)
    return buffer.getvalue()


def build_empty_export() -> bytes:
    """A real export with nothing in it to move."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MARKER, ADMIN)
        archive.writestr('configuration.json', json.dumps({'type': 'CUSTOM'}))
    return buffer.getvalue()


def build_other_zip() -> bytes:
    """A ZIP that VCF Operations did not export."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('beach.txt', 'Not an export. Invented for the demo.')
    return buffer.getvalue()


FILES = {
    'ops-prod-content-export.zip': lambda: build_export(),
    'export-missing-its-dashboards.zip': lambda: build_export(with_dashboards=False),
    'empty-export.zip': build_empty_export,
    'holiday-photos.zip': build_other_zip,
    'damaged-download.zip': lambda: b'PK\x03\x04 cut off after a few byt',
}


def write_all(directory) -> dict:
    """Write every demo file into *directory* and return name -> path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, build in FILES.items():
        path = directory / name
        path.write_bytes(build())
        out[name] = path
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    for name, path in write_all(argv[0]).items():
        print(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
