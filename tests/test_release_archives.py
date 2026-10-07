"""Archive downloads preserve the executable and its launch name."""
import importlib.util
from pathlib import Path
import tarfile
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('release_archive', Path(__file__).parents[1] / 'packaging' / 'archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


@pytest.mark.parametrize('platform', ['linux', 'windows', 'macos-arm64', 'macos-x86_64'])
def test_download_contains_only_the_executable_with_launch_permissions(tmp_path, platform):
    binary = tmp_path / 'build-target'
    payload = b'invented executable bytes'
    binary.write_bytes(payload)
    result = archive.package(binary, platform, tmp_path / 'downloads')
    expected = 'vcfcf-migrator.exe' if platform == 'windows' else 'vcfcf-migrator'
    if platform == 'linux':
        with tarfile.open(result) as packaged:
            assert packaged.getnames() == [expected]
            member = packaged.getmember(expected)
            assert member.mode & 0o111 == 0o111
            assert member.uid == member.gid == 0
            assert packaged.extractfile(member).read() == payload
    else:
        with zipfile.ZipFile(result) as packaged:
            assert packaged.namelist() == [expected]
            assert packaged.getinfo(expected).external_attr >> 16 & 0o111 == 0o111
            assert packaged.read(expected) == payload
