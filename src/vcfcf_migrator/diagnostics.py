"""The shared-report privacy boundary, independent of capture-time redaction.

Only documented structural metadata leaves this boundary unchanged. Every
other value, including names inside errors and unknown dictionary keys, gets
an opaque label. The label table lives for one export and is never serialized.
This also protects reports assembled from old or incompletely redacted events.
Local diagnostic logs are a different, private artifact.
"""
from __future__ import annotations

import math
import re


EVENTS = frozenset('''
bundle.directories bundle.unwritable bundle.written closure.added
closure.not_carried closure.picked command.failed command.refused container.found
container.judged container.rebuilt corpus.absent corpus.checked corpus.walk
corpus.zip_checked document.absent document.unreadable graph.built
grid.columns_defaulted grid.coords_unreadable grid.widened input.directories
input.fingerprint input.listed input.not_a_zip input.not_an_export input.note
input.unreadable item.listed log.contents log.level log.truncated log.failed
log.destination_lost manifest.written marker.absent marker.copied marker.read
marker.unknown_format member.carried_not_inspected member.not_carried member.read
member.unknown member.unreadable node.duplicate node.found object.carries_nothing
output.document output.fingerprint output.unwritable owners.carried page.action
phase.start phase.end preview.built preview.failed preview.kind_not_drawn
preview.not_a_page preview.written ref.ambiguous ref.missing ref.optional_miss
ref.resolved run.crashed run.end run.start scaffolding.dropped scaffolding.narrowed
scaffolding.skipped scaffolding.synthesized selection.closed selection.file_read
selection.line selection.refused selection.resolved selection.unreadable
setting.ignored shape.unhandled swallowed.markup_unparsed
swallowed.widget_view_unreadable widget.clamped widget.classified
widget.order_unreadable widget.placement_unreadable
'''.split())
KINDS = frozenset('''dashboard view supermetric customgroup symptom alert
recommendation report notificationrule notificationtemplate outboundsetting policy'''.split())
LEVELS = frozenset(('error', 'warn', 'info', 'detail', 'debug'))
ENUMS = {
    'event': EVENTS,
    'phase': frozenset(('run', 'build', 'command', 'corpus-zip', 'graph', 'page',
                        'preview-all', 'read', 'select')),
    'lvl': LEVELS, 'level': LEVELS,
    'kind': KINDS, 'to_kind': KINDS, 'wants': KINDS, 'required_by_kind': KINDS,
    'format': frozenset(('jsonl', 'text')),
    'type': frozenset(('ALL', 'CUSTOM')),
    'spelling': frozenset(('uuid', 'name')),
    'action': frozenset(('/open', '/pick-export', '/select', '/select-all', '/clear',
                         '/apply-lines', '/preview', '/filter', '/build', '/settings',
                         '/diagnostics', '/tab', '/disclose', '/inspect', '/tree',
                         '/corpus-check', '/command')),
}
# Measurements produced by the application, never account or content IDs.
MEASUREMENTS = frozenset('''
t ms bytes zip_bytes document_bytes markup_bytes members nodes edges refs index
picked carried added missing not_carried ambiguous unhandled unhandled_shapes
unknown_members events dropped broken cap exit entries entries_kept entries_total
lines objects items documents directories owners_seen dashboards_by_owner
members_written added_by_closure notes errors matches matched zips widgets
empty_widgets elsewhere_widgets navigation_gaps orphan_receivers providers
receivers selectors columns state_blob_chars declared_w declared_x drawn_w drawn_x
'''.split())
# configuration.json's documented aggregate counts. Arbitrary manifest fields
# are never assumed to be counts, even when their values happen to be numbers.
MANIFEST_COUNTS = frozenset('''
costDrivers reports globalSettings alertDefs policies dashboards recommendations
outboundSettings authSources notificationRules views customGroups symptomDefs
dashboardsByOwner superMetrics reportSchedules sdmpCustomApplications users
userGroups userRoles reportSchedulesByOwner configFiles discoveryRules
payloadTemplates appDefAssignments integrations
'''.split())
# Known field *names* carry no identity. Values still require their own policy.
FIELDS = frozenset('''
argv asked_for answered_by carrying command config_keys container context_driven
core corpus_dir corpus_from counts cwd declared destination_lost detail dir dir_from
directory_entries driven_by drives failed failure fields file files from_source
had_source ident keys line log_file manifest marker marker_format member member_names
name note out owner owners path platform python python_build reason renderer
required_by_name required_by_uuid says setting sha256 skipped started state subject
subjects title to_name to_owner to_uuid tool unhandled_types uuid verdict via what
widget widget_type widget_types zip zip_sha256
'''.split()) | MEASUREMENTS | frozenset(ENUMS)


class SharedReport:
    """Allow structural metadata; label everything else, recursively."""

    def __init__(self):
        self._labels = {}

    def label(self, value):
        if value is None:
            return None
        if type(value) not in (str, int, float, bool):
            return '[excluded:unsupported]'
        # Type matters: a numeric account ID is not a count or a string ID.
        key = (type(value).__name__, value)
        if key not in self._labels:
            self._labels[key] = f'item-{len(self._labels) + 1}'
        return self._labels[key]

    def opaque(self, value, depth=0):
        """An untrusted subtree cannot reopen structural metadata exceptions."""
        if depth > 40:
            return '[excluded:depth]'
        if isinstance(value, dict):
            return {self.label(key): self.opaque(item, depth + 1)
                    for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.opaque(item, depth + 1) for item in value]
        return self.label(value)

    def field(self, key, value, parent='', depth=0):
        if depth > 40:
            return '[excluded:depth]'
        if value is None:
            return None
        if isinstance(value, dict):
            if parent == '' and key in ('counts', 'manifest', 'by_level', 'files'):
                return self.document(value, parent=key, depth=depth + 1)
            return self.opaque(value, depth + 1)
        if isinstance(value, (list, tuple)):
            return [self.field(key, item, parent=parent, depth=depth + 1)
                    for item in value]
        if isinstance(value, str):
            if value in ENUMS.get(key, ()):
                return value
            if key in ('tool', 'core', 'python'):
                # Keep release numbers; build labels can include a login name.
                version = re.match(r'\A(\d+\.\d+\.\d+)(?:\D|$)', value)
                if version:
                    return version.group(1)
            if key == 'platform':
                for family in ('macOS', 'Windows', 'Linux', 'Darwin'):
                    if value == family or value.startswith(family + '-'):
                        return family
            return self.label(value)
        is_count = (key in MEASUREMENTS and parent in ('', 'files')
                    or parent == 'counts' and key in KINDS
                    or parent == 'by_level' and key in LEVELS
                    or parent == 'manifest' and key in MANIFEST_COUNTS)
        if is_count:
            if type(value) is int and abs(value) < 2 ** 63:
                return value
            if type(value) is float and math.isfinite(value):
                return value
        # Even numeric identity and boolean-shaped unknown fields fail closed.
        return self.label(value)

    def document(self, document, parent='', depth=0):
        out = {}
        for key, value in document.items():
            allowed = (key in FIELDS or key == 'by_level'
                       or parent == 'counts' and key in KINDS
                       or parent == 'by_level' and key in LEVELS
                       or parent == 'manifest' and key in MANIFEST_COUNTS)
            safe_key = key if allowed else self.label(key)
            out[safe_key] = (self.field(key, value, parent, depth) if allowed
                             else self.opaque(value, depth + 1))
        return out
