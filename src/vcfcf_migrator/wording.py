"""Wording every surface shares.

Operator-facing strings do not say "widget(s)". The rule was written in one
module and the other three kept their parentheses, so the helper lives here
and they all import it.
"""
from __future__ import annotations


def plural(count: int, noun: str, plural_form: str = "") -> str:
    """``1 widget``, ``3 widgets``, ``2 dashboard navigation targets``.

    *plural_form* is for the nouns English does not pluralise with an s.
    """
    if count == 1:
        return f"{count} {noun}"
    return f"{count} {plural_form or noun + 's'}"
