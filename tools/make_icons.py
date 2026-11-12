"""Regenerate the app icon rasters from src/vcfcf_migrator/assets/app.svg.

Needs PySide6 (already a Linux dependency of the app) and nothing else; the
.ico and .icns containers are written here by hand so no Windows or macOS tool
is required. Run:  python tools/make_icons.py
Outputs: packaging/icons/app.ico, app.icns and src/vcfcf_migrator/assets/app.png
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "src/vcfcf_migrator/assets/app.svg"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# (icns type, pixel size): PNG-in-ICNS entries macOS reads natively.
ICNS_TYPES = ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128),
              (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024))


def render(size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
    renderer = QSvgRenderer(str(SVG))
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    if not image.save(buf, "PNG"):
        raise SystemExit(f"could not render {size}px")
    return bytes(data)


def main() -> None:
    cache = {}
    png = lambda s: cache.setdefault(s, render(s))
    entries, images, offset = [], [], 6 + 16 * len(ICO_SIZES)
    for s in ICO_SIZES:
        data = png(s)
        entries.append(struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset))
        images.append(data)
        offset += len(data)
    out = ROOT / "packaging/icons"
    out.mkdir(parents=True, exist_ok=True)
    (out / "app.ico").write_bytes(struct.pack("<HHH", 0, 1, len(ICO_SIZES)) + b"".join(entries) + b"".join(images))
    body = b"".join(t + struct.pack(">I", 8 + len(png(s))) + png(s) for t, s in ICNS_TYPES)
    (out / "app.icns").write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)
    (ROOT / "src/vcfcf_migrator/assets/app.png").write_bytes(png(256))
    print("wrote", out / "app.ico", out / "app.icns")


if __name__ == "__main__":
    main()
