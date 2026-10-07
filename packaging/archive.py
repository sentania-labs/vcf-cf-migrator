"""Package a platform executable under its normal command name."""
from pathlib import Path
import argparse
import tarfile
import zipfile


def package(binary, platform, output):
    binary, output = Path(binary), Path(output)
    name = "vcfcf-migrator.exe" if platform == "windows" else "vcfcf-migrator"
    output.mkdir(parents=True, exist_ok=True)
    suffix = ".tar.gz" if platform == "linux" else ".zip"
    archive = output / ("vcfcf-migrator-" + platform + suffix)
    if platform == "linux":
        with tarfile.open(archive, "w:gz") as bundle:
            info = bundle.gettarinfo(str(binary), arcname=name)
            info.mode = 0o755
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with binary.open("rb") as source:
                bundle.addfile(info, source)
    else:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, binary.read_bytes())
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument("platform", choices=["linux", "windows", "macos-arm64", "macos-x86_64"])
    parser.add_argument("--out", default="archives")
    args = parser.parse_args()
    print(package(args.binary, args.platform, args.out))
