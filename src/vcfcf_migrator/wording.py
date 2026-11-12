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
                     "Local is the default. You can also type a source name. This form signs in with a username and password, not browser SSO.")
EXPORT_PASSWORD_HELP = "Keep this password: you will need it when importing the bundle."
BUILD_PASSWORD_REMINDER = ("Keep the export encryption password you entered when connecting; "
                           "you will need it to import the bundle.")
SOURCE_SWITCH_HELP = "A successful switch to another source discards your current selection."


# The plain name of each kind of content: one of them, several of them, and
# one sentence saying what it is. The words are the ones VCF Operations uses,
# because the person has to find the same things on the target.
KINDS = {
    'dashboard': ('Dashboard', 'Dashboards', 'A page of charts and tables that someone built.'),
    'view': ('View', 'Views', 'A table or chart that dashboards and reports show.'),
    'supermetric': ('Super metric', 'Super metrics',
                    'A calculation built from other measurements, such as an average across a cluster.'),
    'customgroup': ('Group', 'Groups', 'A named set of objects, such as all production clusters.'),
    'symptom': ('Symptom', 'Symptoms', 'A condition to watch for, such as a value going over a limit.'),
    'alert': ('Alert', 'Alerts', 'A warning that is raised when its symptoms are seen.'),
    'recommendation': ('Recommendation', 'Recommendations', 'Advice that is shown with an alert.'),
    'report': ('Report', 'Reports', 'A document made from views and dashboards.'),
    'notificationrule': ('Notification rule', 'Notification rules',
                         'Decides who is told when an alert is raised.'),
    'notificationtemplate': ('Notification template', 'Notification templates',
                             'The wording of a notification.'),
    'outboundsetting': ('Outbound setting', 'Outbound settings',
                        'A place notifications are sent to, such as a mail server.'),
}


def kind_one(kind: str) -> str:
    """``Super metric`` for ``supermetric``; an unknown kind is shown as it is."""
    return KINDS[kind][0] if kind in KINDS else kind.capitalize()


def kind_many(kind: str) -> str:
    return KINDS[kind][1] if kind in KINDS else kind.capitalize()


def kind_what(kind: str) -> str:
    return KINDS[kind][2] if kind in KINDS else ''


def listing(items) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    items = [str(item) for item in items]
    if len(items) < 2:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def short_listing(items, limit: int = 6) -> str:
    """Like ``listing``, but a long list ends in ``and 12 more``."""
    items = [str(item) for item in items]
    if len(items) <= limit:
        return listing(items)
    return ', '.join(items[:limit]) + f' and {len(items) - limit} more'


def kind_counts(counts: dict, order) -> str:
    """``2 dashboards, 1 view, 3 super metrics`` in the given kind order."""
    kinds = [k for k in order if counts.get(k)] + sorted(k for k in counts if k not in order and counts[k])
    return ', '.join(f"{counts[k]} {(kind_one(k) if counts[k] == 1 else kind_many(k)).lower()}" for k in kinds)
