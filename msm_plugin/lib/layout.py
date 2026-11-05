# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Where the tables of a schema diagram go: a layered (Sugiyama) layout.

Pure Python, no shell imports. The tables are the nodes, the foreign keys
the edges, drawn from the referring (child) table to the referenced
(parent) one, so referenced tables sit above the tables that refer to them:

1. Each connected group of tables is laid out on its own; tables without a
   foreign key to or from another one are packed in rows below.
2. Cycles (self references aside, which are left out) are broken by
   reversing the edges a depth-first search finds going back.
3. Layers by longest path from the top, then every parent moved down to
   just above its highest child, so a lookup table does not float far above
   the one table that refers to it.
4. An edge spanning layers gets a dummy node in each layer it passes, and the
   layers are ordered by barycenter sweeps, keeping the order with the
   fewest crossings.
5. x placement: every node pulled towards the mean of its neighbours, the
   least-squares positions that keep the order and the gaps (pool adjacent
   violators); y: a row per layer, as tall as its tallest table.

The result is deterministic: it depends on the tables' order and sizes and
the foreign keys alone. Unlike MySQL Workbench's Autolayout (a hill climber
over figure positions that never counts crossings), it minimises crossings
and keeps Workbench's 80 px minimum gap between figures.

The figure sizes are estimated from the measurements of the MariaDB VS Code
extension's table card (webview/src/modeler/tableCard.ts).
"""

import bisect
import math
from collections import deque
from typing import Optional

from msm_plugin.lib import diagrams

_TABLE = diagrams.FIGURE_KINDS[diagrams.TABLE_FIGURE]

# The gap kept between figures, Workbench's minimum.
GAP = 80
# The gap between the rows of a layout.
ROW_GAP = 80
# What an edge passing a layer takes up in it.
DUMMY_WIDTH = 20
# The space left around a diagram's figures.
MARGIN = 40
# The table card's measurements (tableCard.ts CARD), in diagram units.
_HEADER_HEIGHT = 30
_PADDING = 8
_ROW_HEIGHT = 18
_HEADING_HEIGHT = 16
_SECTION_GAP = 6
_ICON_SIZE = 10
_ICON_GAP = 5
_TITLE_LEFT = 10 + 15 + 5  # TITLE_INSET + TITLE_ICON + gap
_TITLE_RESERVE = 26
# The average width of a character: 11 px rows, 14 px bold titles.
_ROW_CHAR_WIDTH = 6.2
_TITLE_CHAR_WIDTH = 8.4

MIN_FIGURE_WIDTH = 120
MAX_FIGURE_WIDTH = 360

_SWEEPS = 4


# -----------------------------------------------------------------------------
# Figure sizes


def _card_width(title: str, rows: list) -> float:
    """The width a card needs for its title and its rows, each an icon and
    a text. Rounded up to 10 and kept between MIN_FIGURE_WIDTH and
    MAX_FIGURE_WIDTH; the card cuts off what is longer."""
    widest = _TITLE_LEFT + len(title) * _TITLE_CHAR_WIDTH + _TITLE_RESERVE
    for text in rows:
        row = 2 * _PADDING + _ICON_SIZE + 2 * _ICON_GAP + len(text) * _ROW_CHAR_WIDTH
        widest = max(widest, row)
    width = math.ceil(widest / 10) * 10
    return float(min(MAX_FIGURE_WIDTH, max(MIN_FIGURE_WIDTH, width)))


def _card_height(sections: list, figure: Optional[dict]) -> float:
    """The height of a card with these sections expanded, each a heading
    and a row per member; empty ones take no room. A collapsed figure is
    its header."""
    if (figure or {}).get("expanded") is False:
        return float(_HEADER_HEIGHT)
    shown = [rows for rows in sections if rows]
    body = sum(_HEADING_HEIGHT + rows * _ROW_HEIGHT for rows in shown)
    gaps = _SECTION_GAP * max(len(shown) - 1, 0)
    return float(_HEADER_HEIGHT + 2 * _PADDING + body + gaps)


def card_size(title: str, rows: list, sections: list, figure: Optional[dict] = None) -> tuple:
    """The width and height a card needs, see _card_width and _card_height.
    What a figure kind's card shows is diagrams.FIGURE_KINDS' business."""
    return _card_width(title, rows), _card_height(sections, figure)


def figure_width(table: dict) -> float:
    """The width a table's figure needs to show its name and its columns,
    each row an icon, the name and, after it, the type."""
    return _card_width(table.get("name", ""), _TABLE.rows(table))


def figure_height(table: dict, figure: Optional[dict] = None) -> float:
    """The height of a table's card with the figure's sections expanded.

    As the card lays it out: the header, then the columns unless collapsed,
    the indexes and the foreign keys where expanded, each a heading and a
    row per member.
    """
    figure = figure or {}
    return _card_height(_TABLE.sections(table, figure), figure)


# -----------------------------------------------------------------------------
# The layout


def layout(nodes: list, sizes: dict, edges: list) -> dict:
    """Places the nodes of a graph.

    Args:
        nodes: The node names, in the order that breaks ties.
        sizes: Each node's (width, height).
        edges: (child, parent) pairs; the parent goes above. Edges to a node
            not in nodes and self references are left out.

    Returns:
        Each node's (left, top), whole numbers, the top left corner of all at
        (0, 0).
    """
    order = {node: index for index, node in enumerate(nodes)}
    pairs = list(dict.fromkeys(
        (child, parent) for child, parent in edges
        if child in order and parent in order and child != parent
    ))

    blocks = [
        _layout_component(group, sizes, pairs, order)
        for group in _components(nodes, pairs, order)
    ]
    singles = [block for block in blocks if len(block[0]) == 1]
    groups = [block for block in blocks if len(block[0]) > 1]
    groups.sort(key=lambda block: (-len(block[0]), order[next(iter(block[0]))]))

    area = sum((width + GAP) * (height + GAP) for _, width, height in blocks)
    limit = max(
        [width for _, width, _ in blocks] + [math.sqrt(area) * 1.5] if blocks else [0]
    )

    positions = {}
    top = 0.0
    for row in (_rows(groups, limit), _rows(singles, limit)):
        for line in row:
            left = 0.0
            for placed, width, _ in line:
                for node, (x, y) in placed.items():
                    positions[node] = (left + x, top + y)
                left += width + GAP
            top += max(height for _, _, height in line) + ROW_GAP

    result = {node: (round(x), round(y)) for node, (x, y) in positions.items()}
    _check_no_overlap(result, sizes)
    return result


def _components(nodes: list, pairs: list, order: dict) -> list:
    """The connected groups of nodes, each in node order, by their first node."""
    parent = {node: node for node in nodes}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for child, referenced in pairs:
        a, b = find(child), find(referenced)
        if a != b:
            parent[max(a, b, key=order.get)] = min(a, b, key=order.get)
    groups = {}
    for node in nodes:
        groups.setdefault(find(node), []).append(node)
    return list(groups.values())


def _rows(blocks: list, limit: float) -> list:
    """Puts blocks in rows no wider than limit (a wider block gets a row alone)."""
    rows, line, width = [], [], 0.0
    for block in blocks:
        added = block[1] if not line else width + GAP + block[1]
        if line and added > limit:
            rows.append(line)
            line, added = [], block[1]
        line.append(block)
        width = added
    if line:
        rows.append(line)
    return rows


def _layout_component(group: list, sizes: dict, pairs: list, order: dict) -> tuple:
    """Lays out one connected group of nodes.

    Returns:
        ({node: (left, top)} with the group's corner at (0, 0), width, height).
    """
    members = set(group)
    if len(group) == 1:
        width, height = sizes[group[0]]
        return {group[0]: (0.0, 0.0)}, width, height

    edges = [(c, p) for c, p in pairs if c in members]
    up = _acyclic(group, edges)
    layer = _layers(group, up)

    # The layers, with a dummy node in each layer an edge passes.
    rows = [[] for _ in range(max(layer.values()) + 1)]
    widths = {node: sizes[node][0] for node in group}
    links = []
    for node in group:
        rows[layer[node]].append(node)
    for child, parent in up:
        previous = parent
        for depth in range(layer[parent] + 1, layer[child]):
            dummy = ("dummy", child, parent, depth)
            rows[depth].append(dummy)
            widths[dummy] = DUMMY_WIDTH
            links.append((previous, dummy))
            previous = dummy
        links.append((previous, child))

    above = {node: [] for node in widths}
    below = {node: [] for node in widths}
    for upper, lower in links:
        below[upper].append(lower)
        above[lower].append(upper)

    rows = _order(rows, above, below)
    x = _place_x(rows, widths, above, below)

    positions = {}
    top = 0.0
    for row in rows:
        height = max((sizes[node][1] for node in row if node in members), default=0)
        for node in row:
            if node in members:
                positions[node] = (x[node] - widths[node] / 2, top)
        top += height + ROW_GAP

    left = min(position[0] for position in positions.values())
    positions = {node: (px - left, py) for node, (px, py) in positions.items()}
    width = max(positions[node][0] + sizes[node][0] for node in group)
    height = max(positions[node][1] + sizes[node][1] for node in group)
    return positions, width, height


def _acyclic(group: list, edges: list) -> list:
    """The edges as (child, parent), those a depth-first search finds going
    back reversed, so they form no cycle."""
    parents = {node: [] for node in group}
    for child, parent in edges:
        parents[child].append(parent)
    state = {}
    result = []
    for start in group:
        if start in state:
            continue
        state[start] = "open"
        stack = [(start, iter(parents[start]))]
        while stack:
            node, pending = stack[-1]
            following = next(pending, None)
            if following is None:
                state[node] = "done"
                stack.pop()
            elif state.get(following) == "open":
                result.append((following, node))
            else:
                result.append((node, following))
                if following not in state:
                    state[following] = "open"
                    stack.append((following, iter(parents[following])))
    return list(dict.fromkeys(result))


def _layers(group: list, up: list) -> dict:
    """Each node's layer, 0 at the top: below all its parents, and a parent
    as low as its children leave room for."""
    parents = {node: [] for node in group}
    children = {node: [] for node in group}
    for child, parent in up:
        parents[child].append(parent)
        children[parent].append(child)

    # Topological order, parents first, ties by the group's order.
    waiting = {node: len(parents[node]) for node in group}
    ready = deque(node for node in group if waiting[node] == 0)
    topological = []
    while ready:
        node = ready.popleft()
        topological.append(node)
        for child in children[node]:
            waiting[child] -= 1
            if waiting[child] == 0:
                ready.append(child)

    layer = {}
    for node in topological:
        layer[node] = 1 + max((layer[p] for p in parents[node]), default=-1)
    for node in reversed(topological):
        if children[node]:
            layer[node] = max(layer[node], min(layer[c] for c in children[node]) - 1)
    return layer


def _crossings(rows: list, below: dict) -> int:
    """The edge crossings between every pair of neighbouring layers."""
    total = 0
    for upper, lower in zip(rows, rows[1:]):
        position = {node: index for index, node in enumerate(lower)}
        ends = sorted(
            (index, position[child])
            for index, node in enumerate(upper)
            for child in below[node]
        )
        # Pairs out of order among the lower ends.
        seen = []
        for _, end in ends:
            # Lower ends already seen to the right of this one cross it.
            low = bisect.bisect_right(seen, end)
            total += len(seen) - low
            seen.insert(low, end)
    return total


def _order(rows: list, above: dict, below: dict) -> list:
    """Orders each layer by barycenter sweeps, down and up, keeping the
    order with the fewest crossings."""
    rows = [list(row) for row in rows]
    best, best_crossings = [list(row) for row in rows], _crossings(rows, below)
    for _ in range(_SWEEPS):
        for downwards in (True, False):
            indexes = range(1, len(rows)) if downwards else range(len(rows) - 2, -1, -1)
            for index in indexes:
                reference = rows[index - 1] if downwards else rows[index + 1]
                neighbours = above if downwards else below
                position = {node: i for i, node in enumerate(reference)}
                row = rows[index]

                def barycenter(item):
                    i, node = item
                    linked = [position[n] for n in neighbours[node] if n in position]
                    # A node without neighbours there keeps its place.
                    return (sum(linked) / len(linked) if linked else i, i)

                rows[index] = [node for _, node in sorted(enumerate(row), key=barycenter)]
            crossings = _crossings(rows, below)
            if crossings < best_crossings:
                best, best_crossings = [list(row) for row in rows], crossings
    return best


def _separation(a, b, widths: dict) -> float:
    gap = GAP if isinstance(a, str) and isinstance(b, str) else GAP / 2
    return widths[a] / 2 + gap + widths[b] / 2


def _fit(row: list, wanted: dict, widths: dict) -> dict:
    """The centers closest to the wanted ones (least squares) that keep the
    row's order and gaps: pool adjacent violators on the centers less each
    node's offset from the first."""
    offsets = [0.0]
    for a, b in zip(row, row[1:]):
        offsets.append(offsets[-1] + _separation(a, b, widths))
    blocks = []  # [sum, count]
    for node, offset in zip(row, offsets):
        blocks.append([wanted[node] - offset, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    result = {}
    index = 0
    for total, count in blocks:
        for _ in range(count):
            result[row[index]] = total / count + offsets[index]
            index += 1
    return result


def _place_x(rows: list, widths: dict, above: dict, below: dict) -> dict:
    """The x center of every node."""
    x = {}
    for row in rows:
        x.update(_fit(row, {node: 0.0 for node in row}, widths))
    for sweep in range(2 * _SWEEPS):
        downwards = sweep % 2 == 0
        indexes = range(1, len(rows)) if downwards else range(len(rows) - 2, -1, -1)
        for index in indexes:
            row = rows[index]
            neighbours = above if downwards else below
            wanted = {}
            for node in row:
                linked = neighbours[node] + (above[node] + below[node] if sweep >= 2 else [])
                wanted[node] = sum(x[n] for n in linked) / len(linked) if linked else x[node]
            x.update(_fit(row, wanted, widths))
    return x


def _check_no_overlap(positions: dict, sizes: dict) -> None:
    boxes = sorted(
        (left, top, left + sizes[node][0], top + sizes[node][1], node)
        for node, (left, top) in positions.items()
    )
    for index, (left, top, right, bottom, node) in enumerate(boxes):
        for other in boxes[index + 1 :]:
            if other[0] >= right:
                break
            if other[1] < bottom and top < other[3]:
                raise RuntimeError(f"The layout put `{node}` and `{other[4]}` on top of each other.")
