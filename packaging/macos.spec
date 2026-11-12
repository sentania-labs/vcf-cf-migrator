"""Finder bundle shared by the graphical app and CLI launcher."""
from importlib.metadata import version
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
name = 'VCF Content Migrator'
app_version = version('vcf-cf-migrator').split('+')[0].split('.dev')[0]
datas, binaries, hiddenimports = collect_all('webview')
datas += collect_data_files('vcfcf_migrator')
datas += copy_metadata('vcf-cf-migrator') + copy_metadata('vcf-cf-tooling-core')
analysis = Analysis([str(root / 'src/vcfcf_migrator/__main__.py')],
                    pathex=[str(root / 'src')], binaries=binaries, datas=datas,
                    hiddenimports=hiddenimports)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name=name,
          console=False, argv_emulation=False, strip=False, upx=False)
contents = COLLECT(exe, analysis.binaries, analysis.datas, name=name, strip=False, upx=False)
app = BUNDLE(contents, name=name + '.app', icon=str(root / 'packaging/icons/app.icns'), bundle_identifier='net.sentania.vcfcf-migrator',
             version=app_version, info_plist={'CFBundleVersion': app_version})
