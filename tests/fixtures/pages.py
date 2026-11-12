"""A window state over a graph built in a test, without a file on disk."""
from __future__ import annotations

from vcfcf_migrator import insight
from vcfcf_migrator.ui import PageState


def page_over(graph, picks=(), members=None, screen='choose'):
    """The state the window would have with *graph* open and *picks* ticked.

    The caller closes ``state.log`` when it is done.
    """
    state = PageState()
    state.graph, state.insight, state.members = graph, insight.build(graph), members
    state.source_name = 'test-export.zip'
    state._set_picked(list(picks))
    state.screen = screen
    state.max_step = 5
    return state


def visible(html: str) -> str:
    """What a person can read on the page: text, with markup, styles and
    scripts left out, and runs of white space folded to one space."""
    from html.parser import HTMLParser

    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.skip = 0
            self.out = []

        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style', 'title'):
                self.skip += 1

        def handle_endtag(self, tag):
            if tag in ('script', 'style', 'title'):
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip:
                self.out.append(data)

    parser = Text()
    parser.feed(html)
    return ' '.join(' '.join(parser.out).split())


def save_to(state, path, force=False):
    """Save the bundle at *path*, as the Save step does after "Change name or
    folder"."""
    from pathlib import Path
    path = Path(path)
    state.save_name, state.save_folder = path.name, path.parent
    state.save(force=force)
    return state
