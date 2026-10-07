Download the archive for your computer, extract it, and open `vcfcf-migrator` (`vcfcf-migrator.exe` on Windows).

| Computer | Download |
| --- | --- |
| Mac with Apple silicon | `vcfcf-migrator-macos-arm64.zip` |
| Mac with Intel processor | `vcfcf-migrator-macos-x86_64.zip` |
| Linux x86-64 | `vcfcf-migrator-linux.tar.gz` |
| Windows x86-64 | `vcfcf-migrator-windows.zip` |

The application opens a native window. It does not start a local web server.
On Mac, Apple menu > About This Mac shows your chip or processor.
Linux requires a graphical desktop and the system libraries used by Qt.

Open a source export, choose the content to move, and review the bundle before saving.
Builds stop if a selected object has a required dependency missing from the export.
Policies are not carried; check their assignments on the target.

For installation details, command-line use, and current limitations, see the README for this release.
Python users can install the `.whl` with pip instead.
