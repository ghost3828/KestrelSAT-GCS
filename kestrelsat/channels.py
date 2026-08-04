"""Per-channel telemetry state.

Copyright (C) 2026 Wyatt Harris
Written in a personal capacity. This is not a work of the United States
Government and was not prepared in the course of official duties.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from collections import deque
from dataclasses import dataclass
from typing import Any

@dataclass
class Channel:
    """Everything the app knows about one telemetry channel.

    This replaces ten dicts and six dynamically-named instance attributes that
    were all keyed by channel name and had to be kept in step by hand on every
    add and clear. A channel now either exists in full or not at all.
    """

    name: str
    data: deque
    color: str
    color_index: int = 0
    color_is_user: bool = False       # a hand-picked colour survives theme changes
    thickness: int = 2
    dot_size: int = 0
    show_line: bool = True
    visible: bool = True
    display_name: str = ""
    # pyqtgraph
    curve: Any = None
    has_data: bool = False            # is the curve currently holding points
    # the Tk row for this channel
    row: Any = None
    visible_var: Any = None
    name_var: Any = None
    thickness_var: Any = None
    dot_size_var: Any = None
    line_var: Any = None
    color_btn: Any = None

    @property
    def label(self) -> str:
        """Name shown in the legend and the X-axis dropdown."""
        return self.display_name or self.name


