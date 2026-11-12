"""The bundled colours are exactly what the vendor tool derives from the Lattice
tokens, and what it derives is readable.

Oracle: WCAG 2.x contrast (4.5:1 for body text) and the vendor tool's own
function run over the committed token file, so a refresh cannot silently drop
the contrast-safe secondary ink, the link colour or the dark heat bands.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import vendor_lattice  # noqa: E402

from vcfcf_migrator import theme  # noqa: E402


def _theme_values(css: str, name: str) -> dict:
    block = re.search(r'\[data-theme="' + name + r'"\]\s*\{([^}]+)\}', css)
    return dict(re.findall(r'--([\w-]+):\s*([^;]+);', block[1]))


def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_a_vendor_refresh_reproduces_the_committed_heat_bands():
    assert vendor_lattice.heatmap_css(theme.TOKENS_CSS) == theme.HEATMAP_CSS


def test_a_vendor_refresh_keeps_four_distinct_dark_heat_bands():
    dark = _theme_values(vendor_lattice.heatmap_css(theme.TOKENS_CSS), 'dark')
    bands = [dark[f'heat-{band}'] for band in ('good', 'fair', 'poor', 'bad')]
    assert len(set(bands)) == 4


def test_the_page_gets_a_link_colour_in_both_themes():
    assert 'link' in _theme_values(theme.TOKENS_CSS, 'light')
    assert 'link' in _theme_values(theme.TOKENS_CSS, 'dark')


def test_secondary_ink_is_readable_on_the_surface_in_both_themes():
    for name in ('light', 'dark'):
        values = _theme_values(theme.TOKENS_CSS, name)
        assert _contrast(values['ink-subtle'], values['surface']) >= 4.5, name
