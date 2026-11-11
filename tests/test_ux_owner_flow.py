"""Exercise owner choices and warnings through the desktop bridge and CLI."""
import io
import json
import zipfile
from html.parser import HTMLParser

from vcfcf_migrator.cli import main
from vcfcf_migrator.desktop import Bridge
from vcfcf_migrator.ui import PageState


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = False
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden = True
    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = False
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def visible(html):
    parser = Text()
    parser.feed(html)
    return ' '.join(parser.parts)


def make_source(tmp_path, same=False):
    path = tmp_path / 'source.zip'
    users = [{'userId': 'owner-secret-1', 'userName': 'Alice', 'userSourceId': 'directory-1'},
             {'userId': 'owner-secret-2', 'userName': 'Bob', 'userSourceId': 'directory-1'}]
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('configuration.json', json.dumps({'type': 'CUSTOM', 'dashboards': 2}))
        z.writestr('usermappings.json', json.dumps({'sources': [{'id': 'directory-1'}], 'users': users}))
        for i in (1, 2):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as inner:
                inner.writestr('dashboard/dashboard.json', json.dumps({'entries': {}, 'dashboards': [
                    {'id': 'same' if same else f'd{i}', 'name': 'Overview' if same else f'Dashboard {i}', 'widgets': []}]}))
            z.writestr(f'dashboards/owner-secret-{i}', buf.getvalue())
            if i == 1:
                z.writestr('dashboardsharings/owner-secret-1', json.dumps([
                    {'sourceType': 'ACTIVE_DIRECTORY', 'groupName': 'Directory team',
                     'dashboards': [{'dashboardId': 'same' if same else 'd1'}]}]))
    return path


def choose(bridge, key):
    return bridge.act('/select', {'key': key, 'on': '1'})['html']


def test_owner_choices_review_and_real_build(tmp_path, config_dir):
    state = PageState(str(make_source(tmp_path)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:d1@owner-secret-1')
    page = visible(bridge.act('/tab', {'tab': 'review'})['html'])
    assert 'Dashboard ownership will map to local admin' in page
    assert 'Directory sharing will be removed for: Dashboard 1' in page
    assert 'No sharing will remain for: Dashboard 1' in page
    page = visible(bridge.act('/owner', {'owner_mode': 'other', 'target_user': 'target-user'})['html'])
    assert 'will be skipped unless the exact user target-user exists' in page and 'Dashboard 1' in page
    out = tmp_path / 'other.zip'
    bridge.act('/build', {'out': str(out)})
    with zipfile.ZipFile(out) as z:
        assert json.loads(z.read('usermappings.json'))['users'][0]['userName'] == 'target-user'
        assert json.loads(z.read('usermappings.json'))['sources'] == []
    page = visible(bridge.act('/owner', {'owner_mode': 'source'})['html'])
    assert 'will be skipped unless the same users and directories exist' in page
    assert 'Alice' in page and 'Dashboard 1' in page
    out = tmp_path / 'source-owned.zip'
    bridge.act('/build', {'out': str(out)})
    with zipfile.ZipFile(out) as z:
        mapping = json.loads(z.read('usermappings.json'))
        assert mapping['users'][0]['userSourceId'] == 'directory-1'
        assert mapping['sources'] == [{'id': 'directory-1'}]
        assert json.loads(z.read('dashboardsharings/owner-secret-1'))[0]['sourceType'] == 'ACTIVE_DIRECTORY'
    state.log.close()


def test_cli_owner_choices_warn_and_write_requested_mapping(tmp_path, capsys):
    source = make_source(tmp_path)
    picks = tmp_path / 'picks.txt'
    picks.write_text('dashboard:d1@owner-secret-1\n')
    for args, expected in [(['--target-owner', 'target-user'], 'target-user'),
                           (['--keep-directory-ownership'], 'Alice')]:
        out = tmp_path / (expected + '.zip')
        assert main(['build', str(source), '--select', str(picks), '--out', str(out), *args]) == 0
        output = capsys.readouterr()
        assert 'will be skipped unless' in output.err and 'Dashboard 1' in output.err
        with zipfile.ZipFile(out) as z:
            assert json.loads(z.read('usermappings.json'))['users'][0]['userName'] == expected


def test_collision_blocks_review_and_can_remove_a_copy(tmp_path, config_dir):
    state = PageState(str(make_source(tmp_path, same=True)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:same@owner-secret-1')
    choose(bridge, 'dashboard:same@owner-secret-2')
    html = bridge.act('/tab', {'tab': 'review'})['html']
    page = visible(html)
    assert 'Overview' in page and 'Alice' in page and 'Bob' in page
    assert 'owner-secret-1' not in page and 'owner-secret-2' not in page
    assert 'Ready to build' not in page and "disabled>Build bundle" in html
    assert 'Remove Overview' in page
    html = bridge.act('/remove-pick', {'key': 'dashboard:same@owner-secret-2'})['html']
    assert "disabled>Build bundle" not in html
    state.log.close()


def test_direct_metric_and_dangling_view_warnings_are_plain(tmp_path, config_dir):
    sm = '11111111-1111-4111-8111-111111111111'
    source = tmp_path / 'warnings.zip'
    dashboard = {'id': 'dashboard-private-id', 'name': 'Summary', 'widgets': [
        {'id': 'widget-private-id', 'name': 'Capacity view',
         'config': {'viewDefinitionId': 'view-private-id'}}]}
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('configuration.json', json.dumps({'type': 'ALL', 'dashboards': 1, 'views': 1}))
        z.writestr('dashboard/dashboard.json', json.dumps({'dashboards': [dashboard]}))
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, 'w') as inner:
            inner.writestr('content.xml', '<Views><ViewDef id="present"><Title>Present</Title></ViewDef></Views>')
        z.writestr('views.zip', nested.getvalue())
        z.writestr('supermetrics.json', json.dumps({sm: {'name': 'Demand', 'formula': '', 'resourceKinds': []}}))
    state = PageState(str(source))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:dashboard-private-id')
    choose(bridge, 'supermetric:' + sm)
    html = bridge.act('/tab', {'tab': 'review'})['html']
    page = visible(html)
    assert 'Enable these super metrics in a policy on the target: Demand' in page
    assert 'Source policy information may be incomplete' in page
    assert 'Ready to build with warnings' in page
    assert 'Capacity view' in page and 'may already be missing on the source' in page
    assert 'viewDefinitionId' not in page and 'dashboard-private-id' not in page and 'view-private-id' not in page
    out = tmp_path / 'warnings-out.zip'
    html = bridge.act('/build', {'out': str(out)})['html']
    page = visible(html)
    assert out.exists() and 'Capacity view' in page
    assert 'viewDefinitionId' not in page and 'dashboard-private-id' not in page and 'view-private-id' not in page
    state.log.close()


def test_missing_sharing_is_explained_before_build(tmp_path, config_dir):
    state = PageState(str(make_source(tmp_path)))
    bridge = Bridge(state)
    choose(bridge, 'dashboard:d2@owner-secret-2')
    page = visible(bridge.act('/tab', {'tab': 'review'})['html'])
    assert 'No sharing will remain for: Dashboard 2' in page
    assert 'These dashboards will be private; set sharing on the target' in page
    out = tmp_path / 'private.zip'
    page = visible(bridge.act('/build', {'out': str(out)})['html'])
    assert out.exists() and 'These dashboards will be private' in page
    assert 'dashboardsharings/owner-secret-2' not in page
    state.log.close()


def test_keep_source_refuses_missing_directory_information(tmp_path, capsys):
    source = make_source(tmp_path)
    altered = tmp_path / 'missing-source.zip'
    with zipfile.ZipFile(source) as inp, zipfile.ZipFile(altered, 'w') as out:
        for item in inp.infolist():
            data = inp.read(item)
            if item.filename == 'usermappings.json':
                doc = json.loads(data)
                doc['sources'] = []
                data = json.dumps(doc).encode()
            out.writestr(item, data)
    picks = tmp_path / 'picks.txt'
    picks.write_text('dashboard:d1@owner-secret-1\n')
    target = tmp_path / 'bad.zip'
    assert main(['build', str(altered), '--select', str(picks), '--out', str(target),
                 '--keep-directory-ownership']) == 1
    assert not target.exists()
    assert 'missing directory information' in capsys.readouterr().err


def test_cli_collision_identifies_owners_without_raw_ids(tmp_path, capsys):
    source = make_source(tmp_path, same=True)
    out = tmp_path / 'collision.zip'
    assert main(['build', str(source), '--select-all', '--out', str(out)]) == 1
    output = capsys.readouterr().err
    assert 'Overview' in output and 'Alice' in output and 'Bob' in output
    assert 'choose one copy' in output
    assert 'owner-secret-1' not in output and 'owner-secret-2' not in output
    assert not out.exists()
