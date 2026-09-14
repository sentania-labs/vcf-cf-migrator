"""tree, selection and build, against the committed fixture only.

Nothing here reads ``corpus/``. The fixture mirrors the member layout and
the reference spellings of the corpus exports (the PR body names which zip
each shape came from), so CI can run the whole thing on a public runner with
no instance data anywhere near it.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from make_export_fixture import (
    ABSENT_ALERT_ID,
    ABSENT_GROUP_NAME,
    ABSENT_SM_ID,
    ABSENT_SM_NAME,
    GROUP_NAME,
    GROUP_NAME_2,
    GROUP_POLICY_ID,
    DASHBOARD_ID,
    DASHBOARD_ID_2,
    OWNER,
    OWNER_2,
    REPORT_ID,
    RULE_ID,
    SM_IDS,
    TEMPLATE_ID,
    VIEW_IDS,
    build_export_zip,
)
from vcfcf_migrator import containers as _containers
from vcfcf_migrator import graph as _graph
from vcfcf_migrator import selection as _selection
from vcfcf_migrator.cli import main
from vcfcf_migrator.export_reader import read_export, read_members


@pytest.fixture
def graph(export_zip):
    return _graph.build_graph(read_members(export_zip).data)


def _keys(graph, kind, ident):
    return [n.key for n in graph.nodes.values() if n.kind == kind and n.ident == ident]


def _edge_idents(graph, key, kind):
    return {graph.nodes[t].ident for t in graph.edges[key] if graph.nodes[t].kind == kind}


# ---------------------------------------------------------------------------
# The edges
# ---------------------------------------------------------------------------

def test_dashboard_reaches_its_view_and_its_super_metric(graph):
    key = f"dashboard:{DASHBOARD_ID}@{OWNER}"
    assert _edge_idents(graph, key, "view") == {VIEW_IDS[0]}
    # A widget can address a super metric without going through a view.
    assert _edge_idents(graph, key, "supermetric") == {SM_IDS[1]}


def test_view_reaches_its_super_metric(graph):
    assert _edge_idents(graph, f"view:{VIEW_IDS[0]}", "supermetric") == {SM_IDS[0]}


def test_super_metric_reaches_the_one_its_formula_names(graph):
    assert _edge_idents(graph, f"supermetric:{SM_IDS[0]}", "supermetric") == {SM_IDS[1]}


def test_symptom_reaches_the_super_metric_its_threshold_is_on(graph):
    """A symptom's Condition key can be Super Metric|sm_<uuid>. Without this
    edge a bundle built from an alert carries a symptom pointing at a super
    metric the bundle does not hold, and says nothing about it."""
    key = next(k for k in graph.nodes if k.startswith("symptom:"))
    assert _edge_idents(graph, key, "supermetric") == {SM_IDS[1]}


def test_selecting_an_alert_closes_through_the_symptom_to_the_super_metric(graph):
    key = next(k for k in graph.nodes if k.startswith("alert:"))
    picked = _selection.close(graph, [key])
    kinds = sorted(graph.nodes[k].kind for k in picked.keys)
    # The symptom's super metric names a third one, which names a fourth by a
    # name nothing defines: the chain is walked to the end either way.
    assert kinds == ["alert", "recommendation", "supermetric", "supermetric", "symptom"]
    assert any("required by symptom" in a.reason for a in picked.added)


def test_super_metric_reaches_one_its_formula_names_rather_than_uuids(graph):
    """The 8.x spelling, Super Metric|@supermetric:"<Name>". A uuid-shaped
    audit cannot see it, which is how it was missed: six super metrics in the
    8.18.7 corpus export use it and every target is in the same export."""
    assert _edge_idents(graph, f"supermetric:{SM_IDS[1]}", "supermetric") == {SM_IDS[2]}


def test_a_name_that_matches_no_super_metric_is_reported_missing(graph):
    key = f"supermetric:{SM_IDS[2]}"
    assert graph.edges[key] == []
    assert [(m.kind, m.ident) for m in graph.missing_for(key)] == [
        ("supermetric", ABSENT_SM_NAME)]


def test_a_uuid_quoted_in_description_prose_is_not_a_reference(graph):
    """SM 1's description says "Companion to [Fixture] SM 3 (UUID ...)". Real
    exports do exactly this, and reading it as a reference fills the report
    that tells an admin what will break with things that will not."""
    assert _edge_idents(graph, f"supermetric:{SM_IDS[0]}", "supermetric") == {SM_IDS[1]}


def test_two_super_metrics_sharing_a_name_are_both_carried(tmp_path):
    """The factory picks one by project scope; an export has no projects to
    pick by, so guessing wrong would mean a bundle missing the super metric
    the formula meant. Both are carried and the ambiguity is reported."""
    src = zipfile.ZipFile(io.BytesIO(build_export_zip()))
    twin = "44444444-4444-4444-8444-444444444444"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in src.namelist():
            data = src.read(name)
            if name == "supermetrics.json":
                doc = json.loads(data)
                doc[twin] = dict(doc[SM_IDS[2]], name="[Fixture] SM 3")
                data = json.dumps(doc).encode()
            z.writestr(name, data)
    path = tmp_path / "twins.zip"
    path.write_bytes(out.getvalue())
    graph = _graph.build_graph(read_members(path).data)
    assert _edge_idents(graph, f"supermetric:{SM_IDS[1]}", "supermetric") == {SM_IDS[2], twin}
    # Exactly one ambiguity, and it names both uuids so they can be told apart.
    assert len(graph.ambiguous) == 1
    assert "2 different objects answer to" in graph.ambiguous[0]
    assert SM_IDS[2] in graph.ambiguous[0] and twin in graph.ambiguous[0]
    assert "references by name that several objects answer to" in _graph.render_tree(graph)
    # The command that writes the bundle says it too, not only tree.
    picked = _selection.close(graph, [f"supermetric:{SM_IDS[1]}"])
    assert picked.ambiguous == graph.ambiguous
    assert "references by name that several objects answer to" in _selection.render(graph, picked)
    assert _selection.as_dict(graph, picked)["ambiguous"] == graph.ambiguous


def test_a_dashboard_under_two_owners_is_not_an_ambiguity(graph):
    """Two nodes of one object, which is the deliberate two-owner case. The
    guard that was meant to suppress it compared key suffixes, and a dashboard
    key ends in uuid@owner, so it never matched and the case reported as an
    ambiguity naming a uuid under a by-name heading."""
    assert graph.edges[f"report:{REPORT_ID}"]
    assert graph.ambiguous == []


def test_a_dashboard_scoped_to_a_custom_group_reaches_it_by_name(graph):
    """A widget scoped to a group binds to it as a resource, by name. Custom
    groups carry no uuid in an export, so by name is the only spelling there
    is. The object shape carries it as resourceName next to a Container
    resource kind."""
    key = f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}"
    assert _edge_idents(graph, key, "customgroup") == {GROUP_NAME}


def test_a_list_shaped_resource_scope_reaches_the_group_too(graph):
    """The same scope written the other way an export writes it: a list of
    {"name", "id"}, with no resourceName and no resource kind at all. A regex
    for the object shape matches nothing here, which is why extraction walks
    the parsed structure instead."""
    key = f"dashboard:{DASHBOARD_ID}@{OWNER}"
    assert _edge_idents(graph, key, "customgroup") == {GROUP_NAME_2}


def test_a_membership_rule_naming_no_group_here_is_not_a_missing_edge(graph):
    """Most ruleStringValues name ordinary resources. Reporting every one as a
    missing dependency would drown the one report an admin relies on."""
    key = f"customgroup:{GROUP_NAME}"
    assert [m.kind for m in graph.missing_for(key)] == ["policy"]
    assert graph.edges[key] == []


def test_only_a_relationship_rule_names_another_group(graph):
    """A ResourceNameRule names a resource, never a group, even when its value
    is a group's name. Across the corpus 20 of 28 rule values are name or
    metric rules carrying words like template, vms and group, so a group named
    any of those would be pulled into a bundle that does not depend on it."""
    # Prod Clusters' only RelationshipRule names a group that is not here, and
    # its ResourceNameRule names Web Tier, which is. Neither is an edge.
    assert graph.edges[f"customgroup:{GROUP_NAME}"] == []
    # Web Tier's RelationshipRule names Prod Clusters, which is.
    assert _edge_idents(graph, f"customgroup:{GROUP_NAME_2}", "customgroup") == {GROUP_NAME}


def test_a_carried_group_says_its_policy_will_be_missing(graph):
    """policies.xml is a member this tool does not understand and never
    carries, so a carried group's policy is always absent on the target. The
    report that says what will break has to say it."""
    gaps = graph.missing_for(f"customgroup:{GROUP_NAME}")
    assert [(g.kind, g.ident) for g in gaps] == [("policy", GROUP_POLICY_ID)]
    assert "policies.xml" in gaps[0].via


def test_a_notification_rule_resource_condition_reaches_a_group_by_name(graph):
    """The same binding a widget writes, in a different document under a
    different key: ResourceID.resourceName inside a RESOURCE_AND_CHILD
    condition."""
    other = next(n for n in graph.nodes.values()
                 if n.kind == "notificationrule" and n.ident != RULE_ID)
    assert _edge_idents(graph, other.key, "customgroup") == {GROUP_NAME}


def test_alert_reaches_its_symptom_and_recommendation(graph):
    key = next(k for k in graph.nodes if k.startswith("alert:"))
    assert _edge_idents(graph, key, "symptom")
    assert _edge_idents(graph, key, "recommendation")


def test_notification_rule_reaches_alert_template_and_endpoint(graph):
    key = f"notificationrule:{RULE_ID}"
    assert _edge_idents(graph, key, "alert") == {"AlertDefinition-VMWARE-Fixture_Cluster_CPU"}
    assert _edge_idents(graph, key, "notificationtemplate") == {TEMPLATE_ID}
    # The second rule names the endpoint the export carries.
    other = next(n for n in graph.nodes.values()
                 if n.kind == "notificationrule" and n.ident != RULE_ID)
    assert _edge_idents(graph, other.key, "outboundsetting")


def test_report_reaches_its_view_and_its_dashboard(graph):
    key = f"report:{REPORT_ID}"
    assert _edge_idents(graph, key, "view") == {VIEW_IDS[0]}
    assert _edge_idents(graph, key, "dashboard") == {DASHBOARD_ID}


def test_a_dashboard_under_two_owners_is_two_nodes(graph):
    assert sorted(_keys(graph, "dashboard", DASHBOARD_ID)) == sorted(
        [f"dashboard:{DASHBOARD_ID}@{OWNER}", f"dashboard:{DASHBOARD_ID}@{OWNER_2}"])


def test_an_edge_out_of_the_export_is_named_and_counted_not_an_error(graph):
    idents = {(m.kind, m.ident) for m in graph.missing}
    assert ("supermetric", ABSENT_SM_ID) in idents
    assert ("alert", ABSENT_ALERT_ID) in idents
    assert ("outboundsetting", "WebhookPlugin/fixture") in idents
    text = _graph.render_tree(graph)
    assert f"MISSING supermetric [{ABSENT_SM_ID}]" in text
    assert ("supermetric", ABSENT_SM_NAME) in idents
    assert f'MISSING supermetric [{ABSENT_SM_NAME}]' in text
    assert "edges to objects this export does not carry: 5" in text
    # A membership rule naming no group in this export is normal, not missing.
    assert ABSENT_GROUP_NAME not in text


def test_tree_counts_match_inspect(export_zip, graph):
    assert graph.counts() == read_export(export_zip).counts()


def test_tree_json_carries_nodes_edges_and_missing(export_zip, capsys):
    assert main(["tree", "--json", str(export_zip)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["counts"]["dashboard"] == 3
    assert doc["missing"] and doc["nodes"] and doc["edges"]
    assert "policies.xml" in doc["unknown_members"]
    # The marker is scaffolding the builder copies, not an unreadable member.
    assert not any(n.endswith("L.v1") for n in doc["unknown_members"])


# ---------------------------------------------------------------------------
# Selection and closure
# ---------------------------------------------------------------------------

def test_selecting_a_dashboard_pulls_in_its_view_and_super_metrics(graph):
    picked = _selection.close(graph, [f"dashboard:{DASHBOARD_ID}@{OWNER}"])
    assert set(picked.keys) == {
        f"dashboard:{DASHBOARD_ID}@{OWNER}", f"view:{VIEW_IDS[0]}",
        f"supermetric:{SM_IDS[0]}", f"supermetric:{SM_IDS[1]}", f"supermetric:{SM_IDS[2]}",
        # Reached by the list-shaped resource scope, which in turn reaches the
        # group its own RelationshipRule names.
        f"customgroup:{GROUP_NAME_2}", f"customgroup:{GROUP_NAME}"}
    reasons = [a.reason for a in picked.added]
    # The reason names the uuid as well as the name, so two objects sharing a
    # display name do not render as two identical lines.
    assert any(f"view [Fixture] Cluster List [{VIEW_IDS[0]}] added: required by dashboard"
               in r for r in reasons)
    assert any(f"supermetric [Fixture] SM 1 [{SM_IDS[0]}] added: required by view"
               in r for r in reasons)


def test_closure_reports_a_dependency_the_export_does_not_carry(graph):
    picked = _selection.close(graph, [f"view:{VIEW_IDS[1]}"])
    assert [m.ident for m in picked.missing] == [ABSENT_SM_ID]
    assert "it will be missing on import" in _selection.render(graph, picked)


def test_a_line_naming_nothing_is_refused(graph):
    with pytest.raises(_selection.BadSelection) as e:
        _selection.resolve(graph, ["00000000-0000-4000-8000-000000000000"])
    assert "does not carry" in str(e.value)


@pytest.mark.parametrize("line,expect_kind", [
    (VIEW_IDS[0], "view"),
    (f"view:{VIEW_IDS[0]}", "view"),
    ("customgroup:[Fixture] Prod Clusters", "customgroup"),
    ("outboundsetting:StandardEmailPlugin/[Fixture] Mail relay", "outboundsetting"),
])
def test_every_selection_spelling_resolves(graph, line, expect_kind):
    hits = _selection.match_line(graph, line)
    assert hits and all(graph.nodes[h].kind == expect_kind for h in hits)


def test_an_owner_qualified_line_picks_exactly_one_copy(graph):
    hits = _selection.match_line(graph, f"dashboard:{DASHBOARD_ID}@{OWNER_2}")
    assert hits == [f"dashboard:{DASHBOARD_ID}@{OWNER_2}"]
    assert len(_selection.match_line(graph, f"dashboard:{DASHBOARD_ID}")) == 2


def test_comments_and_blank_lines_are_ignored(tmp_path):
    path = tmp_path / "sel.txt"
    path.write_text(f"# a comment\n\n{VIEW_IDS[0]}  # trailing\n")
    assert _selection.parse_selection_file(path) == [VIEW_IDS[0]]


# ---------------------------------------------------------------------------
# Build: what came out is what went in
# ---------------------------------------------------------------------------

def _build(tmp_path, export_zip, lines=None, all_of_it=False, version="9.0.2"):
    out = tmp_path / "bundle.zip"
    argv = ["--source-version", version, "build", str(export_zip), "--out", str(out)]
    if all_of_it:
        argv.append("--select-all")
    else:
        sel = tmp_path / "sel.txt"
        sel.write_text("\n".join(lines or []) + "\n")
        argv += ["--select", str(sel)]
    return out, main(argv)


def test_select_all_round_trips_every_item(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, all_of_it=True)
    assert code == 0
    assert read_export(out).counts() == read_export(export_zip).counts()


def test_every_carried_document_is_byte_identical(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, all_of_it=True)
    assert code == 0
    source = _containers.documents(read_members(export_zip).data)
    built = _containers.documents(read_members(out).data)
    assert built.keys() == source.keys()
    for key, raw in source.items():
        assert built[key] == raw, key


def test_select_all_leaves_every_container_structurally_identical(tmp_path, export_zip):
    """Documents are copied, containers are rebuilt, so the container is the
    half that can drift. A select-all drops nothing, so nothing may change:
    this is the check that catches a rebuild quietly reshaping a member."""
    out, code = _build(tmp_path, export_zip, all_of_it=True)
    assert code == 0
    source = _containers.container_shapes(read_members(export_zip).data)
    built = _containers.container_shapes(read_members(out).data)
    assert [k for k in source if k in built and built[k] != source[k]] == []
    # Only members the tool cannot read may be absent.
    graph = _graph.build_graph(read_members(export_zip).data)
    assert sorted(k for k in source if k not in built) == sorted(graph.unknown_members)


def test_select_all_preserves_both_name_map_shapes(tmp_path, export_zip):
    """8.x writes ruleNameToTemplateNameMap's entry as a single object, 9.x as
    a list. The fixture carries one block of each, and a select-all must
    reshape neither."""
    out, code = _build(tmp_path, export_zip, all_of_it=True)
    assert code == 0
    source = json.loads(zipfile.ZipFile(export_zip).read("notificationrules.json"))
    built = json.loads(zipfile.ZipFile(out).read("notificationrules.json"))
    key = "ruleNameToTemplateNameMap"
    assert built["NotificationRules"][key] == source["NotificationRules"][key]
    assert isinstance(source["NotificationRules"][key][1]["entry"], dict)


def test_a_map_pair_is_judged_against_the_whole_bundle_not_one_member(tmp_path, export_zip):
    """The rules live in notificationrules.json and the templates they name
    live in payloadtemplates.json, so judging a mapping against its own member
    alone drops every mapping on an export shaped that way."""
    graph = _graph.build_graph(read_members(export_zip).data)
    rules = [n.key for n in graph.by_kind("notificationrule")]
    templates = [n.key for n in graph.by_kind("notificationtemplate")]
    out, code = _build(tmp_path, export_zip, rules + templates)
    assert code == 0
    doc = json.loads(zipfile.ZipFile(out).read("notificationrules.json"))
    assert doc["NotificationRules"]["ruleNameToTemplateNameMap"] == json.loads(
        zipfile.ZipFile(export_zip).read("notificationrules.json")
    )["NotificationRules"]["ruleNameToTemplateNameMap"]


def test_a_dropped_map_pair_is_named_in_the_build_report(tmp_path, export_zip, capsys):
    graph = _graph.build_graph(read_members(export_zip).data)
    other = next(n for n in graph.nodes.values()
                 if n.kind == "notificationrule" and n.ident != RULE_ID)
    _out, code = _build(tmp_path, export_zip, [other.key])
    assert code == 0
    out = capsys.readouterr().out
    assert "ruleNameToTemplateNameMap: dropped the mapping from rule" in out


@pytest.mark.parametrize("member,kind", [
    ("views.zip", "view"),
    ("reports.zip", "report"),
    ("supermetrics.json", "supermetric"),
    ("customgroups.json", "customgroup"),
    ("symptomdefs.xml", "symptom"),
    ("alertdefs.xml", "alert"),
    ("recommendationdefs.xml", "recommendation"),
    ("notificationrules.json", "notificationrule"),
    ("payloadtemplates.json", "notificationtemplate"),
    ("outboundsettings.json", "outboundsetting"),
])
def test_each_container_is_rebuilt_with_only_what_was_picked(tmp_path, export_zip, member, kind):
    """One object of each kind, on its own: the container has to come back
    readable with exactly that one document in it."""
    graph = _graph.build_graph(read_members(export_zip).data)
    node = graph.by_kind(kind)[0]
    out, code = _build(tmp_path, export_zip, [node.key])
    assert code == 0
    # The closure decides how many of this kind ride along (one super metric
    # names another), and the rebuilt container must hold exactly those.
    closed = _selection.close(graph, [node.key])
    expected = sorted(n.ident for n in _selection.selected_nodes(graph, closed) if n.kind == kind)
    rebuilt = _graph.build_graph(read_members(out).data)
    assert sorted(n.ident for n in rebuilt.by_kind(kind)) == expected
    assert member in zipfile.ZipFile(out).namelist()
    assert _containers.documents(read_members(out).data)[(member, kind, node.ident, node.owner)] \
        == _containers.documents(read_members(export_zip).data)[(member, kind, node.ident, node.owner)]


def test_dashboards_are_rebuilt_per_owner_inner_zip(tmp_path, export_zip):
    node = f"dashboard:{DASHBOARD_ID}@{OWNER}"
    out, code = _build(tmp_path, export_zip, [node])
    assert code == 0
    names = zipfile.ZipFile(out).namelist()
    assert f"dashboards/{OWNER}" in names
    assert f"dashboards/{OWNER_2}" not in names
    inner = zipfile.ZipFile(io.BytesIO(zipfile.ZipFile(out).read(f"dashboards/{OWNER}")))
    doc = json.loads(inner.read("dashboard/dashboard.json"))
    assert [d["id"] for d in doc["dashboards"]] == [DASHBOARD_ID]
    # Container material that belongs to no one dashboard rides along.
    assert "entries" in doc and "uuid" in doc
    assert "dashboard/resources/resources.properties" in inner.namelist()


def test_a_selection_crossing_two_owners_keeps_both_inner_zips(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, [
        f"dashboard:{DASHBOARD_ID}@{OWNER}",
        f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}",
    ])
    assert code == 0
    names = zipfile.ZipFile(out).namelist()
    assert f"dashboards/{OWNER}" in names and f"dashboards/{OWNER_2}" in names
    listed = read_export(out)
    assert listed.counts()["dashboard"] == 2
    manifest = listed.manifest
    assert sorted(manifest["dashboardsByOwner"], key=lambda e: e["owner"]) == sorted(
        [{"owner": OWNER, "count": 1}, {"owner": OWNER_2, "count": 1}], key=lambda e: e["owner"])


def test_the_same_uuid_under_two_owners_can_both_be_carried(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, [f"dashboard:{DASHBOARD_ID}"])
    assert code == 0
    assert read_export(out).counts()["dashboard"] == 2
    source = _containers.documents(read_members(export_zip).data)
    built = _containers.documents(read_members(out).data)
    for owner in (OWNER, OWNER_2):
        key = (f"dashboards/{owner}", "dashboard", DASHBOARD_ID, owner)
        assert built[key] == source[key]


def test_a_subset_bundle_carries_the_closure_and_nothing_more(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, [f"dashboard:{DASHBOARD_ID}@{OWNER}"])
    assert code == 0
    assert read_export(out).counts() == {"dashboard": 1, "view": 1, "supermetric": 3,
                                        "customgroup": 2}
    names = zipfile.ZipFile(out).namelist()
    assert "reports.zip" not in names and "alertdefs.xml" not in names


def test_the_manifest_counts_what_was_carried(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, [f"dashboard:{DASHBOARD_ID}@{OWNER}"])
    assert code == 0
    manifest = json.loads(zipfile.ZipFile(out).read("configuration.json"))
    assert manifest["dashboards"] == 1 and manifest["views"] == 1 and manifest["superMetrics"] == 3
    assert manifest["type"] == "CUSTOM"
    assert "signature" not in manifest


def test_the_marker_is_copied_byte_for_byte(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, all_of_it=True)
    assert code == 0
    source = zipfile.ZipFile(export_zip)
    marker = next(n for n in source.namelist() if n.endswith("L.v1"))
    assert zipfile.ZipFile(out).read(marker) == source.read(marker)


def test_a_member_the_tool_does_not_understand_is_never_carried(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, all_of_it=True)
    assert code == 0
    assert "policies.xml" not in zipfile.ZipFile(out).namelist()


def test_scaffolding_is_narrowed_to_what_the_bundle_holds(tmp_path, export_zip):
    out, code = _build(tmp_path, export_zip, [f"dashboard:{DASHBOARD_ID_2}@{OWNER_2}"])
    assert code == 0
    zf = zipfile.ZipFile(out)
    users = json.loads(zf.read("usermappings.json"))["users"]
    assert [u["userId"] for u in users] == [OWNER_2]
    sharings = json.loads(zf.read(f"dashboardsharings/{OWNER_2}"))
    assert [d["dashboardId"] for g in sharings for d in g["dashboards"]] == [DASHBOARD_ID_2]
    assert f"dashboardsharings/{OWNER}" not in zf.namelist()


def test_the_rule_to_template_map_drops_pairs_the_bundle_does_not_hold(tmp_path, export_zip):
    """The name map is the one thing rebuilt from a parsed value: a mapping to
    an uncarried template would send the import looking for something absent."""
    graph = _graph.build_graph(read_members(export_zip).data)
    other = next(n for n in graph.nodes.values()
                 if n.kind == "notificationrule" and n.ident != RULE_ID)
    out, code = _build(tmp_path, export_zip, [other.key])
    assert code == 0
    doc = json.loads(zipfile.ZipFile(out).read("notificationrules.json"))
    blocks = doc["NotificationRules"]["ruleNameToTemplateNameMap"]
    # The other rule's own mapping survives, with the 8.x object shape it had.
    assert blocks == [{"entry": {"string": [other.name, "[Fixture] Host template"]}}]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_a_selection_naming_a_missing_uuid_writes_no_bundle(tmp_path, export_zip, capsys):
    out, code = _build(tmp_path, export_zip, ["00000000-0000-4000-8000-000000000000"])
    assert code == 1
    assert not out.exists()
    err = capsys.readouterr().err
    assert "does not carry" in err and "no bundle written" in err


def test_build_refuses_without_a_declared_source_version(tmp_path, export_zip, capsys):
    out = tmp_path / "b.zip"
    assert main(["build", str(export_zip), "--select-all", "--out", str(out)]) == 1
    assert not out.exists()
    assert "no source version declared" in capsys.readouterr().err


def test_build_refuses_below_the_floor(tmp_path, export_zip, capsys):
    out = tmp_path / "b.zip"
    assert main(["--source-version", "8.6", "build", str(export_zip),
                 "--select-all", "--out", str(out)]) == 1
    assert not out.exists()
    assert "below the floor" in capsys.readouterr().err


def test_build_needs_exactly_one_of_select_and_select_all(tmp_path, export_zip, capsys):
    out = tmp_path / "b.zip"
    assert main(["--source-version", "9.0.2", "build", str(export_zip), "--out", str(out)]) == 2
    assert "exactly one of" in capsys.readouterr().err


def test_an_empty_selection_file_writes_no_bundle(tmp_path, export_zip, capsys):
    out, code = _build(tmp_path, export_zip, ["# nothing here"])
    assert code == 1
    assert not out.exists()
    assert "selection is empty" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# corpus-check
# ---------------------------------------------------------------------------

def test_corpus_check_reports_one_line_per_zip(tmp_path, capsys):
    (tmp_path / "a.zip").write_bytes(build_export_zip())
    (tmp_path / "b.zip").write_bytes(b"not a zip at all")
    (tmp_path / "versions.json").write_text(json.dumps({"a.zip": "9.0.2"}))
    assert main(["--source-version", "9.0.2", "corpus-check", str(tmp_path)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("corpus:")
    assert any(l.startswith("ok       a.zip") and "documents byte-identical" in l for l in out)
    assert any(l.startswith("error    b.zip") for l in out)


def test_corpus_check_never_writes_into_the_corpus(tmp_path, capsys):
    (tmp_path / "a.zip").write_bytes(build_export_zip())
    before = sorted(p.name for p in tmp_path.iterdir())
    assert main(["--source-version", "9.0.2", "corpus-check", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_corpus_check_refuses_rather_than_guessing_a_version(tmp_path, capsys):
    (tmp_path / "a.zip").write_bytes(build_export_zip())
    assert main(["corpus-check", str(tmp_path)]) == 0
    assert "build needs a declared source version" in capsys.readouterr().out


def test_corpus_check_on_a_missing_directory_exits_one(tmp_path, capsys):
    assert main(["corpus-check", str(tmp_path / "nope")]) == 1
    assert "does not exist" in capsys.readouterr().out
