from __future__ import annotations

import lib  # noqa: F401  (sets sys.path)
from lib import clock, page, top_bar

import analyst_view
import behind_scenes

page("Capitation Revenue Check")
view = top_bar()
now = clock()
if view == "Behind the scenes":
    behind_scenes.render(now)
else:
    analyst_view.render(now)
