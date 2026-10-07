"""Dashboard navigation IDs, as observed in Operations dashboard exports."""


def links(doc):
    """Return (source widget, destination dashboard, destination widgets)."""
    result, errors = [], []
    nav = doc.get('dashboardNavigations', {})
    if nav is None:
        return [], []
    if not isinstance(nav, dict):
        return [], ['navigation map is not an object']
    for source, targets in nav.items():
        if not isinstance(targets, list):
            errors.append('navigation targets are not a list')
            continue
        for target in targets:
            if not isinstance(target, dict) or not isinstance(target.get('id'), str) or not target['id'].strip():
                errors.append('navigation destination has no dashboard ID')
                continue
            widgets = target.get('widgets')
            if not isinstance(widgets, list):
                errors.append('navigation destination widgets are not a list')
                widgets = []
            ids = []
            for widget in widgets:
                if not isinstance(widget, dict) or not isinstance(widget.get('id'), str) or not widget['id'].strip():
                    errors.append('navigation destination widget has no ID')
                else:
                    ids.append(widget['id'])
            result.append((source, target['id'], ids))
    return result, errors


def widget_ids(doc):
    """IDs of defined widgets, excluding references inside navigation links."""
    ids = set()

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'dashboardNavigations':
                    continue
                if key == 'widgets' and isinstance(child, list):
                    ids.update(w['id'] for w in child if isinstance(w, dict)
                               and isinstance(w.get('id'), str))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(doc)
    return ids


def missing_targets(docs):
    """Count destination entries absent from the entire export, across owners."""
    ids = {doc.get('id') for doc in docs if isinstance(doc.get('id'), str)}
    return sum(target not in ids for doc in docs for _, target, _ in links(doc)[0])
