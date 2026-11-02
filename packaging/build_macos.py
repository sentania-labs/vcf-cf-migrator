"""Build the Finder application and its command-line launcher."""
from pathlib import Path
import shutil
import subprocess
import sys


APP_NAME = 'VCF Content Migrator'


def main():
    if sys.platform != 'darwin':
        raise SystemExit('The Mac application must be built on macOS.')
    root = Path(__file__).resolve().parents[1]
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
        '--workpath', 'build/macos-app', '--distpath', 'dist', 'packaging/macos.spec',
    ], cwd=root, check=True)
    app = root / 'dist' / (APP_NAME + '.app')
    if not (app / 'Contents' / 'MacOS' / APP_NAME).is_file():
        raise SystemExit('The build did not produce the expected application executable.')
    launcher = app / 'Contents' / 'MacOS' / 'vcfcf-migrator'
    shutil.copy2(root / 'packaging' / 'macos-cli-launcher', launcher)
    launcher.chmod(0o755)
    print(app)


if __name__ == '__main__':
    main()
