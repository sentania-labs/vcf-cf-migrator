"""Deterministic mock values for the preview.

The preview exists so an admin can recognise an object before deciding to
carry it, and recognising it means seeing the shape of the thing: a table with
the columns the view defines, a scoreboard with the tiles the widget declares.
Those need values, and the tool has none: it reads an export, never an
instance.

So the values are made up, and two rules keep made-up values honest.

**Deterministic.** Every value is derived by hash from the strings the export
already carries (a metric key, a column name, a row number), never from a
random source and never from the clock. The same export previews identically
on every run and on every workstation, so two admins comparing notes are
looking at the same page, and a test can assert on byte-identical output.

**Never mistaken for real.** Object names in mock rows are visibly synthetic
(``sample object 3``), and every preview surface that carries a mock value
says so in its own words. Names and labels, by contrast, are never invented:
those come from the export, because a label the tool made up would be the one
thing that could mislead an admin about what the object is.

Magnitudes follow the unit the export declares (a percent lands in 0 to 100, a
latency in milliseconds lands in single or double digits), so a value looks
plausible against its own column heading rather than looking like a hash.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional, Sequence

# Neutral placeholder strings for a column the export declares as text. They
# carry no meaning on purpose: a made-up hostname or status would read as
# something observed.
STRING_VALUES = (
    "sample text A", "sample text B", "sample text C", "sample text D",
    "sample text E", "sample text F", "sample text G", "sample text H",
)

# unit id fragment -> (low, high, decimals). Matched as a substring of the
# lowercased unit id or unit name the export carries, longest first, so
# ``percent`` beats ``cent`` and ``msec`` beats ``sec``.
UNIT_RANGES = (
    ("percent", (0.5, 99.5, 1)),
    ("ratio", (0.0, 1.0, 2)),
    ("msec", (0.2, 240.0, 1)),
    ("millisecond", (0.2, 240.0, 1)),
    ("microsecond", (5.0, 9000.0, 0)),
    ("second", (0.0, 3600.0, 0)),
    ("minute", (1.0, 1440.0, 0)),
    ("hour", (1.0, 720.0, 1)),
    ("day", (1.0, 365.0, 0)),
    ("currency", (10.0, 9000.0, 2)),
    ("dollar", (10.0, 9000.0, 2)),
    ("khz", (800.0, 3600.0, 0)),
    ("mhz", (800.0, 3600.0, 0)),
    ("ghz", (1.0, 4.0, 2)),
    ("kbps", (4.0, 90000.0, 0)),
    ("kb", (64.0, 900000.0, 0)),
    ("mb", (16.0, 64000.0, 0)),
    ("gb", (1.0, 2048.0, 1)),
    ("tb", (0.1, 120.0, 2)),
    ("iops", (10.0, 12000.0, 0)),
    ("count", (0.0, 400.0, 0)),
    ("core", (1.0, 128.0, 0)),
    ("vcpu", (1.0, 64.0, 0)),
    ("watt", (40.0, 900.0, 0)),
    ("celsius", (18.0, 45.0, 1)),
)

# Key fragments that say what a metric is when the export declares no unit.
KEY_RANGES = (
    ("usage_average", (1.0, 99.0, 1)),
    ("usage", (1.0, 99.0, 1)),
    ("workload", (1.0, 99.0, 1)),
    ("demand", (1.0, 99.0, 1)),
    ("latency", (0.2, 90.0, 1)),
    ("iops", (10.0, 12000.0, 0)),
    ("throughput", (10.0, 90000.0, 0)),
    ("cost", (10.0, 9000.0, 2)),
    ("count", (0.0, 400.0, 0)),
    ("num_cpu", (1.0, 64.0, 0)),
    ("vcpu", (1.0, 64.0, 0)),
    ("number", (1.0, 400.0, 0)),
    ("capacity", (10.0, 4000.0, 0)),
    ("health", (40.0, 100.0, 0)),
    ("size", (1.0, 2048.0, 1)),
)

DEFAULT_RANGE = (1.0, 1000.0, 1)


def _digest(*parts: object) -> bytes:
    """One stable digest over the strings that identify a value.

    ``\\x1f`` joins the parts so ``("ab", "c")`` and ``("a", "bc")`` cannot
    collide, which would make two different columns share a value for a
    reason no reader could see.
    """
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).digest()


def unit_seed(*parts: object) -> int:
    """A stable non-negative integer for *parts*. The one entropy source here."""
    return int.from_bytes(_digest(*parts)[:8], "big")


def _fraction(*parts: object) -> float:
    """A stable value in [0, 1) for *parts*."""
    return unit_seed(*parts) / float(1 << 64)


def range_for(unit: Optional[str] = None, key: str = "") -> Sequence:
    """(low, high, decimals) for a metric, from its declared unit or its key.

    The unit the export declares wins; the key is the fallback, because plenty
    of metrics carry ``metricUnitId: -1`` (VCF Operations' "default unit") and
    a percentage rendered as 743.2 reads as a bug in the preview rather than
    as a made-up number.
    """
    for haystack, table in ((str(unit or "").lower(), UNIT_RANGES),
                            (str(key or "").lower(), KEY_RANGES)):
        if not haystack:
            continue
        hits = [(len(frag), rng) for frag, rng in table if frag in haystack]
        if hits:
            return max(hits, key=lambda pair: pair[0])[1]
    return DEFAULT_RANGE


def number(key: str, unit: Optional[str] = None, row: int = 0,
           label: str = "") -> float:
    """A mock number for *key* (and *row*), in the magnitude its unit implies."""
    low, high, decimals = range_for(unit, key or label)
    value = low + _fraction("number", key, label, unit, row) * (high - low)
    return round(value, decimals)


def format_number(value: float, decimals: Optional[int] = None) -> str:
    """A number as the preview prints it: grouped thousands, no trailing zeros
    beyond the decimals its magnitude warrants."""
    if decimals is None:
        decimals = 0 if abs(value) >= 1000 or float(value).is_integer() else 2
    return f"{value:,.{decimals}f}"


def metric_value(key: str, unit: Optional[str] = None, row: int = 0,
                 label: str = "") -> str:
    """The printable mock value for a metric column or tile."""
    low, high, decimals = range_for(unit, key or label)
    value = number(key, unit, row, label)
    return format_number(value, decimals)


def string_value(key: str, row: int = 0, label: str = "") -> str:
    """The printable mock value for a column the export declares as text."""
    return STRING_VALUES[unit_seed("string", key, label, row) % len(STRING_VALUES)]


def object_name(key: str, row: int) -> str:
    """The stand-in for a monitored object's name in a mock row.

    Visibly synthetic on purpose. An invented hostname is the one mock value
    an admin could mistake for something the export carried.
    """
    return f"sample object {row + 1}"


def sample_name(word: str, row: int) -> str:
    """A visibly synthetic stand-in for a named thing of some kind, used where
    a widget shows a list the export does not populate (an alert list names no
    alerts until it runs). ``sample alert 2`` cannot be mistaken for a name the
    export carried."""
    return f"sample {word} {row + 1}"


def series(key: str, points: int = 24, unit: Optional[str] = None) -> List[float]:
    """A mock series for a chart: a walk, not independent samples.

    Independent hashed samples draw a barcode, which tells an admin nothing
    about whether a widget is a trend chart. A walk inside the unit's own
    range draws something a chart-shaped widget plausibly holds.
    """
    low, high, decimals = range_for(unit, key)
    span = high - low
    value = low + _fraction("series", key, "start") * span
    out: List[float] = []
    for i in range(points):
        step = (_fraction("series", key, i) - 0.5) * span * 0.18
        value = min(high, max(low, value + step))
        out.append(round(value, decimals))
    return out


def ranked(key: str, count: int, unit: Optional[str] = None) -> List[float]:
    """Mock values in descending order, for a bar chart that ranks objects."""
    values = [number(key, unit, row=i) for i in range(count)]
    return sorted(values, reverse=True)


def pick(options: Sequence[str], *parts: object) -> str:
    """One of *options*, chosen stably from *parts*. Empty options is a
    programming error here, not a data case, so it raises."""
    if not options:
        raise ValueError("pick needs at least one option")
    return options[unit_seed("pick", *parts) % len(options)]
