"""Refresh the bundled Lattice color contract from its generated distribution."""
import json
from pathlib import Path
import pprint
import sys


def main():
    dist = Path(sys.argv[1])
    target = Path(__file__).resolve().parents[1] / 'src/vcfcf_migrator/theme.py'
    palette = json.loads((dist / 'lattice-palette.json').read_text())
    tokens = (dist / 'tokens.css').read_text().split(':root {')[0]
    current = target.read_text()
    prefix = current[:current.index('PALETTE = ')]
    aliases = current[current.index('ALIASES = '):]
    target.write_text(prefix + 'PALETTE = ' + pprint.pformat(palette, sort_dicts=False)
                      + '\n\nTOKENS_CSS = ' + repr(tokens) + '\n\n' + aliases)


if __name__ == '__main__':
    main()
