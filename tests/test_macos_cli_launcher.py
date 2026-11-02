"""The Mac bundle's command-line launcher runs the app and installs itself on PATH."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

LAUNCHER = Path(__file__).parents[1] / 'packaging' / 'macos-cli-launcher'

pytestmark = pytest.mark.skipif(
    os.name == 'nt' or (os.path.isdir('/usr/local/bin') and os.access('/usr/local/bin', os.W_OK)),
    reason='needs POSIX sh and a /usr/local/bin the test cannot write to')


@pytest.fixture
def bundle(tmp_path):
    macos = tmp_path.resolve() / 'VCF Content Migrator.app' / 'Contents' / 'MacOS'
    macos.mkdir(parents=True)
    launcher = macos / 'vcfcf-migrator'
    shutil.copy2(LAUNCHER, launcher)
    app = macos / 'VCF Content Migrator'
    app.write_text('#!/bin/sh\nprintf "app:%s\\n" "$*"\n')
    app.chmod(0o755)
    home = tmp_path.resolve() / 'home'
    home.mkdir()
    return launcher, home


def run(command, home, path='/usr/bin:/bin'):
    env = {'HOME': str(home), 'PATH': path}
    return subprocess.run(command, env=env, capture_output=True, text=True)


def test_arguments_reach_the_app(bundle):
    launcher, home = bundle
    result = run([str(launcher), 'inspect', 'a b.zip'], home)
    assert result.returncode == 0
    assert result.stdout == 'app:inspect a b.zip\n'


def test_install_links_launcher_and_the_link_runs_the_app(bundle):
    launcher, home = bundle
    result = run([str(launcher), 'install-cli'], home)
    link = home / '.local' / 'bin' / 'vcfcf-migrator'
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == f'Installed CLI at {link}'
    assert f'Add {link.parent} to your PATH' in result.stdout
    assert link.is_symlink() and Path(os.readlink(link)) == launcher
    assert run([str(link), 'version'], home).stdout == 'app:version\n'


def test_install_stays_quiet_about_path_when_bin_is_on_it(bundle):
    launcher, home = bundle
    path = f'{home / ".local" / "bin"}:/usr/bin:/bin'
    result = run([str(launcher), 'install-cli'], home, path)
    assert result.returncode == 0
    assert 'PATH' not in result.stdout


def test_install_from_the_installed_link_is_idempotent(bundle):
    launcher, home = bundle
    run([str(launcher), 'install-cli'], home)
    link = home / '.local' / 'bin' / 'vcfcf-migrator'
    result = run([str(link), 'install-cli'], home)
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == f'CLI already installed at {link}'
    assert Path(os.readlink(link)) == launcher


def test_install_refuses_to_replace_something_else(bundle):
    launcher, home = bundle
    link = home / '.local' / 'bin' / 'vcfcf-migrator'
    link.parent.mkdir(parents=True)
    link.write_text('someone else\n')
    result = run([str(launcher), 'install-cli'], home)
    assert result.returncode == 1
    assert 'already exists' in result.stderr
    assert link.read_text() == 'someone else\n'


def test_install_rejects_extra_arguments(bundle):
    launcher, home = bundle
    result = run([str(launcher), 'install-cli', 'extra'], home)
    assert result.returncode == 2
    assert 'Usage' in result.stderr
    assert not (home / '.local').exists()
