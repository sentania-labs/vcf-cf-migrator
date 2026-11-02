"""Refresh the bundled Lattice color contract from its generated distribution."""
import json
from pathlib import Path
import pprint
import re
import sys


def mix(first, second, weight):
    """Blend Lattice colors while generating, for older native WebKit support."""
    channels = [round(int(first[i:i + 2], 16) * (1 - weight)
                      + int(second[i:i + 2], 16) * weight) for i in (1, 3, 5)]
    return '#' + ''.join(f'{value:02x}' for value in channels)


def heatmap_css(tokens):
    """Four data bands derived from Lattice, distinct even when bad == bad-ink.

    Light warning ink is meant for text; tint it with warning surface for the
    yellow data band. Blend that band toward danger for the orange band. No
    independent color literals or browser color-mix support are needed.
    """
    result = []
    for name, selector in [('light', ':root, [data-theme="light"]'),
                           ('dark', '[data-theme="dark"]')]:
        block = re.search(r'\[data-theme="' + name + r'"\]\s*\{([^}]+)\}', tokens)
        if block is None:
            raise ValueError(f'Lattice has no {name} theme')
        colors = dict(re.findall(r'--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;', block[1]))
        fair = mix(colors['warn'], colors['warn-soft'], .7) if name == 'light' else colors['warn']
        bands = {'good': colors['ok'], 'fair': fair,
                 'poor': mix(fair, colors['bad'], .5), 'bad': colors['bad']}
        result.append(selector + ' {\n' + ''.join(
            f'  --heat-{band}: {color};\n' for band, color in bands.items()) + '}\n')
    return '\n'.join(result)


def main():
    dist = Path(sys.argv[1])
    target = Path(__file__).resolve().parents[1] / 'src/vcfcf_migrator/theme.py'
    palette = json.loads((dist / 'lattice-palette.json').read_text())
    tokens = (dist / 'tokens.css').read_text().split(':root {')[0]
    current = target.read_text()
    prefix = current[:current.index('PALETTE = ')]
    aliases = current[current.index('ALIASES = '):]
    target.write_text(prefix + 'PALETTE = ' + pprint.pformat(palette, sort_dicts=False)
                      + '\n\nTOKENS_CSS = ' + repr(tokens)
                      + '\n\nHEATMAP_CSS = ' + repr(heatmap_css(tokens)) + '\n\n' + aliases)


if __name__ == '__main__':
    main()
