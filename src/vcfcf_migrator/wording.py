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


LOGIN_SOURCE_HELP = ("Where this account is managed: Local, or a directory or SSO source "
                     "configured in VCF Operations. Ask your administrator if unsure. "
                     "Leave blank to use Local, the default.")
EXPORT_PASSWORD_HELP = "Keep this password: you will need it when importing the bundle."
BUILD_PASSWORD_REMINDER = ("Keep the export encryption password you entered when connecting; "
                           "you will need it to import the bundle.")
REGRESSION_FOLDER_LABEL = "Regression test export folder (developer)"
REGRESSION_FOLDER_HELP = "Checks every zip in this folder for compatibility; does not change the exports."
SOURCE_SWITCH_HELP = "A successful switch to another source discards your current selection."
