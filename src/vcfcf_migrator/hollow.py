"""Dashboards the source never finished loading, and exports that lost some.

A dashboard imported into VCF Operations materialises in two phases. The
second runs on its own and, when it cannot resolve what the dashboard binds
to (an adapter that is not installed, an object with no instances), never
finishes. Such a dashboard is exported with its import flag false, the
bindings it was still waiting on, and widgets with empty configuration. The
dependency walk finds few or no references in it, so without a check it would
read as a dashboard with nothing missing. It is the opposite: its references
are unknown, and a bundle built from it cannot work on the target.

An export can also be taken at a moment when the dashboards are not there:
its contents list says it holds some, and the dashboard files are empty or
absent. That is an export to take again, not an instance without dashboards.

This module reads both from the parsed documents and owns the wording the
page and the command line share. Nothing here touches a file or the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from vcfcf_migrator.wording import plural

# One phrase everywhere a person is told about it.
NOT_FINISHED = "not finished loading on the source"
NOT_FINISHED_CAP = NOT_FINISHED[:1].upper() + NOT_FINISHED[1:]
NOT_TRUSTED = ("Its references cannot be trusted, so it is not shown as having none. "
               "A bundle built from it would carry a dashboard that was not working on the source "
               "and will not work on the target.")
LEFT_OUT = "Left out of the selection by default; select it by name to include it anyway."


@dataclass(frozen=True)
class Unfinished:
    """What the source's own record says about a dashboard that did not load."""
    attempts: int = 0
    waiting_on: tuple = field(default_factory=tuple)


def _false(value) -> bool:
    if value is False:
        return True
    return isinstance(value, str) and value.strip().lower() == "false"


def _entry_text(family: str, entry) -> str:
    """One thing the dashboard was waiting on, in words a person can act on."""
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ""
    adapter = entry.get("adapterKindKey")
    kind = entry.get("resourceKindKey") or entry.get("resourceKindId")
    name = entry.get("name")
    adapter = adapter.strip() if isinstance(adapter, str) else ""
    kind = kind.strip() if isinstance(kind, str) else ""
    name = name.strip() if isinstance(name, str) else ""
    pair = " / ".join(p for p in (adapter, kind) if p)
    if family == "adapterKind":
        return f"the {adapter} adapter" if adapter else ""
    if family == "resourceKind":
        return f"the resource kind {pair}" if pair else ""
    if name:
        return f"the object {name}" + (f" ({pair})" if pair else "")
    return f"an object of kind {pair}" if pair else ""


def waiting_on(entry_keys) -> List[str]:
    """What the pending bindings name, in document order and without repeats.

    A binding with nothing readable in it is counted as one unnamed item
    rather than dropped, so a dashboard that was waiting is never reported as
    waiting on nothing.
    """
    out: List[str] = []
    unnamed = 0
    if isinstance(entry_keys, dict):
        for family, entries in entry_keys.items():
            items = entries if isinstance(entries, list) else [entries]
            for entry in items:
                if entry in (None, [], {}, ""):
                    continue
                text = _entry_text(str(family), entry)
                if not text:
                    unnamed += 1
                elif text not in out:
                    out.append(text)
    if unnamed:
        out.append(plural(unnamed, "item the export does not name", "items the export does not name"))
    return out


def unfinished(doc) -> Optional[Unfinished]:
    """The dashboard's loading state, or None when the source finished it.

    Only a flag that says false counts. A document with no such flag (an older
    export, a hand-made one) is taken as finished: nothing says otherwise.
    """
    if not isinstance(doc, dict) or not _false(doc.get("importComplete")):
        return None
    attempts = doc.get("importAttempts")
    return Unfinished(attempts=attempts if isinstance(attempts, int) and not isinstance(attempts, bool)
                      and attempts > 0 else 0,
                      waiting_on=tuple(waiting_on(doc.get("entryKeys"))))


def waiting_text(waiting, limit: int = 3) -> str:
    """``Waiting on the X adapter, the object Y and 2 more.``"""
    waiting = list(waiting)
    if not waiting:
        return ""
    shown, rest = waiting[:limit], len(waiting) - len(waiting[:limit])
    if rest:
        text = ", ".join(shown) + f" and {rest} more"
    elif len(shown) > 1:
        text = ", ".join(shown[:-1]) + " and " + shown[-1]
    else:
        text = shown[0]
    return f"Waiting on {text}."


def detail_lines(attempts: int, waiting) -> List[str]:
    """The sentences that explain one dashboard, for a page or a report."""
    lines = []
    waiting = list(waiting)
    if waiting:
        lines.append(waiting_text(waiting, limit=len(waiting)))
    else:
        lines.append("The export does not say what it was waiting on.")
    if attempts:
        lines.append(f"The source tried to finish it {plural(attempts, 'time')}.")
    return lines


def summary_line(count: int) -> str:
    return f"{plural(count, 'dashboard')} {NOT_FINISHED}"


# -- the export as a whole ---------------------------------------------------

def declared_dashboards(manifest) -> Optional[int]:
    """How many dashboards the export's contents list says it holds, if it says."""
    if not isinstance(manifest, dict):
        return None
    count = manifest.get("dashboards")
    if isinstance(count, int) and not isinstance(count, bool):
        return count
    per_owner = manifest.get("dashboardsByOwner")
    if isinstance(per_owner, list):
        counts = [item.get("count") for item in per_owner if isinstance(item, dict)]
        if counts and all(isinstance(c, int) and not isinstance(c, bool) for c in counts):
            return sum(counts)
    return None


def incomplete_export_message(manifest, parsed: int) -> Optional[str]:
    """Why this export looks incomplete, or None when its dashboards add up.

    Zero declared is fine: an instance can have none. Declared and not found,
    or a different number found, is an export that lost something on the way.
    """
    declared = declared_dashboards(manifest)
    if declared is None or declared <= 0 or declared == parsed:
        return None
    base = f"This export looks incomplete: it says it holds {plural(declared, 'dashboard')}, "
    if parsed == 0:
        base += "but its dashboard files are empty or missing."
    else:
        base += f"but {parsed} could be read from it."
    return base + " Take the export again from the source and open the new one."
