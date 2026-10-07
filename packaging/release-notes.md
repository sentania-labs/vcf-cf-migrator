Dashboard navigation now includes linked dashboards automatically. Missing destination dashboards or referenced widgets block bundle creation, including links in built-in content. Include the missing dashboards in the source export, repair broken widget links in Operations, or remove affected selections. There is no override.

The app shows a small link beside its version when a newer stable release is available. The check runs quietly in the background; clicking the link opens the release page in your browser. You choose when to download and install an update.

Download the archive for your computer and extract it. On Mac, move **VCF Content Migrator.app** to Applications and open it. On Windows, open `vcfcf-migrator.exe`; on Linux, run `./vcfcf-migrator`.

| Computer | Download |
| --- | --- |
| Mac with Apple silicon | `vcfcf-migrator-macos-arm64-app.zip` |
| Mac with Intel processor | `vcfcf-migrator-macos-x86_64-app.zip` |
| Linux x86-64 | `vcfcf-migrator-linux.tar.gz` |
| Windows x86-64 | `vcfcf-migrator-windows.zip` |

Mac CLI users can use the separate `vcfcf-migrator-macos-arm64.zip` or
`vcfcf-migrator-macos-x86_64.zip` downloads. Both contain `vcfcf-migrator`.
The Mac app is signed, notarized, and carries its notarization ticket.

The application opens a native window. It does not start a local web server.
On Mac, Apple menu > About This Mac shows your chip or processor.
Linux requires a graphical desktop and the system libraries used by Qt.

Open a source export or connect directly to Operations, choose the content to move, and review the bundle before saving.
Builds stop if a selected object has a required dependency missing from the export.
Policies are not carried; check their assignments on the target.

For installation details, command-line use, and current limitations, see the README for this release.
Python users can install the `.whl` with pip instead.
