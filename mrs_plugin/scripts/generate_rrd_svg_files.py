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

"""Renders the railroad diagrams (RRD) of the MRS grammar as SVG files.

Writes one SVG per parser rule of grammar/MRSParser.g4 to docs/images/sql,
the images the SQL reference (docs/sections/sql/*.md) shows. Run it after
every grammar change:

    python3 scripts/generate_rrd_svg_files.py [--check] [--prune] [rule ...]

    --check   only report which files would change, write nothing
    --prune   delete SVG files of rules that no longer exist
    rule ...  render only these rules

The output is what the "Save to SVG" export of the ANTLR4 VS Code extension
(mike-lischke.vscode-antlr4 2.4) produced with the settings the docs used
("antlr4.rrd.stripNamePart": "_SYMBOL|_OPERATOR", "antlr4.rrd.wrapAfter": 50)
and its patches (DATABASE shown as SCHEMA, the docs' style sheet): its
diagram generator and the railroad-diagrams library by Tab Atkins Jr. (CC0)
are ported here. One difference: the height of a diagram covers everything
drawn in it. The library computed some heights too small, which cut off the
bottom of the image (and were fixed by hand before).

Only the Python standard library is needed.
"""

import argparse
import math
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GRAMMAR_FILE = os.path.join(SCRIPT_DIR, "..", "grammar", "MRSParser.g4")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "docs", "images", "sql")

# The extension settings and patches of the MRS docs
STRIP_PATTERN = re.compile(r"_SYMBOL|_OPERATOR")
WRAP_AFTER = 50

# The railroad diagram rules of the style sheet, embedded into every file
STYLES = (
    'svg.railroad-diagram path { stroke-width: 2; stroke: darkgray; fill: rgba(0, 0, 0, 0); }'
    'svg.railroad-diagram text { font: bold 12px Hack, "Source Code Pro", monospace; '
    'text-anchor: middle; fill: #404040; }'
    'svg.railroad-diagram text.comment { font: italic 10px Hack, "Source Code Pro", monospace; '
    'fill: #404040; }'
    'svg.railroad-diagram g.terminal rect { stroke-width: 2; stroke: #404040; '
    'fill: rgba(200, 200, 200, 0.8); }'
    'svg.railroad-diagram g.non-terminal rect { stroke-width: 2; stroke: #404040; '
    'fill: rgba(255, 255, 255, 1); }'
    'svg.railroad-diagram text.diagram-text { font-size: 12px Hack, "Source Code Pro", '
    'monospace; fill: red; }'
    'svg.railroad-diagram path.diagram-text { stroke-width: 1; stroke: red; fill: red; '
    'cursor: help; }'
)


# -- Numbers as JavaScript prints them --------------------------------------


def js_num(value):
    """String(value) of a JavaScript number."""
    if isinstance(value, bool):
        raise TypeError("not a number")
    if float(value).is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(float(value))
    if "e" in text:
        mantissa, exponent = text.split("e")
        exponent = int(exponent)
        if -7 < exponent < 21:
            text = format(float(value), "f").rstrip("0").rstrip(".")
        else:
            text = mantissa.rstrip("0").rstrip(".") + "e" + ("+" if exponent > 0 else "-") + str(abs(exponent))
    return text


def js_str(value):
    return js_num(value) if isinstance(value, (int, float)) else str(value)


# -- The railroad-diagrams library (the subset the generator uses) ----------


class Options:
    VS = 16  # minimum vertical separation (the extension sets 16)
    AR = 10  # radius of arcs
    DIAGRAM_CLASS = "railroad-diagram"
    STROKE_ODD_PIXEL_LENGTH = True
    INTERNAL_ALIGNMENT = "center"
    CHAR_WIDTH = 8.5
    COMMENT_CHAR_WIDTH = 7


def determine_gaps(outer, inner):
    diff = outer - inner
    if Options.INTERNAL_ALIGNMENT == "left":
        return [0, diff]
    if Options.INTERNAL_ALIGNMENT == "right":
        return [diff, 0]
    return [diff / 2, diff / 2]


class FakeSVG:
    def __init__(self, tag_name, attrs=None, text=None):
        self.children = text if text else []
        self.tag_name = tag_name
        self.attrs = dict(attrs) if attrs else {}

    def add_to(self, parent):
        parent.children.append(self)
        return self


class Path(FakeSVG):
    def __init__(self, x, y):
        super().__init__("path")
        self.attrs["d"] = "M" + js_num(x) + " " + js_num(y)

    def m(self, x, y):
        self.attrs["d"] += "m" + js_num(x) + " " + js_num(y)
        return self

    def h(self, val):
        self.attrs["d"] += "h" + js_num(val)
        return self

    def right(self, val):
        return self.h(max(0, val))

    def left(self, val):
        return self.h(-max(0, val))

    def v(self, val):
        self.attrs["d"] += "v" + js_num(val)
        return self

    def down(self, val):
        return self.v(max(0, val))

    def up(self, val):
        return self.v(-max(0, val))

    def arc(self, sweep):
        x = Options.AR
        y = Options.AR
        if sweep[0] == "e" or sweep[1] == "w":
            x *= -1
        if sweep[0] == "s" or sweep[1] == "n":
            y *= -1
        cw = 1 if sweep in ("ne", "es", "sw", "wn") else 0
        self.attrs["d"] += (
            "a" + js_num(Options.AR) + " " + js_num(Options.AR) + " 0 0 " + str(cw)
            + " " + js_num(x) + " " + js_num(y)
        )
        return self


class DiagramMultiContainer(FakeSVG):
    def __init__(self, tag_name, items, attrs=None, text=None):
        super().__init__(tag_name, attrs, text)
        self.items = [item if isinstance(item, FakeSVG) else Terminal(str(item)) for item in items]


class Start(FakeSVG):
    def __init__(self, type="simple"):
        super().__init__("g")
        self.width = 20
        self.height = 0
        self.up = 10
        self.down = 10
        self.needs_space = False
        self.type = type

    def format(self, x, y, width=None):
        path = Path(x, y - 10)
        if self.type == "complex":
            path.down(20).m(0, -10).right(self.width).add_to(self)
        else:
            path.down(20).m(10, -20).down(20).m(-10, -10).right(self.width).add_to(self)
        return self


class End(FakeSVG):
    def __init__(self, type="simple"):
        super().__init__("path")
        self.width = 20
        self.height = 0
        self.up = 10
        self.down = 10
        self.needs_space = False
        self.type = type

    def format(self, x, y, width=None):
        if self.type == "complex":
            self.attrs["d"] = "M " + js_num(x) + " " + js_num(y) + " h 20 m 0 -10 v 20"
        else:
            self.attrs["d"] = "M " + js_num(x) + " " + js_num(y) + " h 20 m -10 -10 v 20 m 10 -20 v 20"
        return self


class Diagram(DiagramMultiContainer):
    def __init__(self, *items, complex=False):
        super().__init__("svg", list(items), {"class": Options.DIAGRAM_CLASS})
        self.items.insert(0, Start())
        self.items.append(End())
        self.up = self.down = self.height = self.width = 0
        for item in self.items:
            self.width += item.width + (20 if item.needs_space else 0)
            self.up = max(self.up, item.up - self.height)
            self.height += item.height
            self.down = max(self.down - item.height, item.down)
        if complex:
            self.items[0] = Start("complex")
            self.items[-1] = End("complex")

    def format(self, padding=20):
        x = padding
        y = padding + self.up
        g = FakeSVG("g", {"transform": "translate(.5 .5)"} if Options.STROKE_ODD_PIXEL_LENGTH else {})
        for item in self.items:
            if item.needs_space:
                Path(x, y).h(10).add_to(g)
                x += 10
            item.format(x, y, item.width).add_to(g)
            x += item.width
            y += item.height
            if item.needs_space:
                Path(x, y).h(10).add_to(g)
                x += 10
        self.attrs["width"] = self.width + padding * 2
        self.attrs["height"] = self.up + self.height + self.down + padding * 2
        self.attrs["viewBox"] = "0 0 " + js_num(self.attrs["width"]) + " " + js_num(self.attrs["height"])
        g.add_to(self)
        return self


class Sequence(DiagramMultiContainer):
    def __init__(self, *items):
        super().__init__("g", list(items))
        self.needs_space = True
        self.up = self.down = self.height = self.width = 0
        for item in self.items:
            self.width += item.width + (20 if item.needs_space else 0)
            self.up = max(self.up, item.up - self.height)
            self.height += item.height
            self.down = max(self.down - item.height, item.down)
        if self.items[0].needs_space:
            self.width -= 10
        if self.items[-1].needs_space:
            self.width -= 10

    def format(self, x, y, width):
        gaps = determine_gaps(width, self.width)
        Path(x, y).h(gaps[0]).add_to(self)
        Path(x + gaps[0] + self.width, y + self.height).h(gaps[1]).add_to(self)
        x += gaps[0]
        last = len(self.items) - 1
        for i, item in enumerate(self.items):
            if item.needs_space and i > 0:
                Path(x, y).h(10).add_to(self)
                x += 10
            item.format(x, y, item.width).add_to(self)
            x += item.width
            y += item.height
            if item.needs_space and i < last:
                Path(x, y).h(10).add_to(self)
                x += 10
        return self


class Stack(DiagramMultiContainer):
    def __init__(self, *items):
        super().__init__("g", list(items))
        self.width = max(item.width + (20 if item.needs_space else 0) for item in self.items)
        if len(self.items) > 1:
            self.width += Options.AR * 2
        self.needs_space = True
        self.up = self.items[0].up
        self.down = self.items[-1].down
        self.height = 0
        last = len(self.items) - 1
        for i, item in enumerate(self.items):
            self.height += item.height
            if i > 0:
                self.height += max(Options.AR * 2, item.up + Options.VS)
            if i < last:
                self.height += max(Options.AR * 2, item.down + Options.VS)

    def format(self, x, y, width):
        gaps = determine_gaps(width, self.width)
        Path(x, y).h(gaps[0]).add_to(self)
        x += gaps[0]
        x_initial = x
        if len(self.items) > 1:
            Path(x, y).h(Options.AR).add_to(self)
            x += Options.AR
        for i, item in enumerate(self.items):
            inner_width = self.width - (Options.AR * 2 if len(self.items) > 1 else 0)
            item.format(x, y, inner_width).add_to(self)
            x += inner_width
            y += item.height
            if i != len(self.items) - 1:
                (Path(x, y)
                    .arc("ne").down(max(0, item.down + Options.VS - Options.AR * 2))
                    .arc("es").left(inner_width)
                    .arc("nw").down(max(0, self.items[i + 1].up + Options.VS - Options.AR * 2))
                    .arc("ws").add_to(self))
                y += (max(item.down + Options.VS, Options.AR * 2)
                      + max(self.items[i + 1].up + Options.VS, Options.AR * 2))
                x = x_initial + Options.AR
        if len(self.items) > 1:
            Path(x, y).h(Options.AR).add_to(self)
            x += Options.AR
        Path(x, y).h(gaps[1]).add_to(self)
        return self


class Choice(DiagramMultiContainer):
    def __init__(self, normal, *items):
        super().__init__("g", list(items))
        self.normal = normal
        first = 0
        last = len(items) - 1
        self.width = max(item.width for item in self.items) + Options.AR * 4
        self.height = self.items[normal].height
        self.up = self.items[first].up
        for i in range(first, normal):
            arcs = Options.AR * 2 if i == normal - 1 else Options.AR
            self.up += max(arcs, self.items[i].height + self.items[i].down
                           + Options.VS + self.items[i + 1].up)
        self.down = self.items[last].down
        for i in range(normal + 1, last + 1):
            arcs = Options.AR * 2 if i == normal + 1 else Options.AR
            self.down += max(arcs, self.items[i - 1].height + self.items[i - 1].down
                             + Options.VS + self.items[i].up)
        self.down -= self.items[normal].height
        self.needs_space = False

    def format(self, x, y, width):
        gaps = determine_gaps(width, self.width)
        Path(x, y).h(gaps[0]).add_to(self)
        Path(x + gaps[0] + self.width, y + self.height).h(gaps[1]).add_to(self)
        x += gaps[0]
        last = len(self.items) - 1
        inner_width = self.width - Options.AR * 4

        distance = 0
        for i in range(self.normal - 1, -1, -1):
            item = self.items[i]
            if i == self.normal - 1:
                distance = max(Options.AR * 2, self.items[self.normal].up + Options.VS
                               + item.down + item.height)
            Path(x, y).arc("se").up(distance - Options.AR * 2).arc("wn").add_to(self)
            item.format(x + Options.AR * 2, y - distance, inner_width).add_to(self)
            (Path(x + Options.AR * 2 + inner_width, y - distance + item.height)
                .arc("ne").down(distance - item.height + self.height - Options.AR * 2)
                .arc("ws").add_to(self))
            distance += max(Options.AR, item.up + Options.VS
                            + (0 if i == 0 else self.items[i - 1].down + self.items[i - 1].height))

        Path(x, y).right(Options.AR * 2).add_to(self)
        self.items[self.normal].format(x + Options.AR * 2, y, inner_width).add_to(self)
        Path(x + Options.AR * 2 + inner_width, y + self.height).right(Options.AR * 2).add_to(self)

        for i in range(self.normal + 1, last + 1):
            item = self.items[i]
            if i == self.normal + 1:
                distance = max(Options.AR * 2, self.height + self.items[self.normal].down
                               + Options.VS + item.up)
            Path(x, y).arc("ne").down(distance - Options.AR * 2).arc("ws").add_to(self)
            item.format(x + Options.AR * 2, y + distance, inner_width).add_to(self)
            (Path(x + Options.AR * 2 + inner_width, y + distance + item.height)
                .arc("se").up(distance - Options.AR * 2 + item.height - self.height)
                .arc("wn").add_to(self))
            distance += max(Options.AR, item.height + item.down + Options.VS
                            + (0 if i == last else self.items[i + 1].up))
        return self


class Skip(FakeSVG):
    def __init__(self):
        super().__init__("g")
        self.width = self.height = self.up = self.down = 0
        self.needs_space = False

    def format(self, x, y, width):
        Path(x, y).right(width).add_to(self)
        return self


def Optional(item):
    return Choice(1, Skip(), item)


class OneOrMore(FakeSVG):
    def __init__(self, item, rep=None):
        super().__init__("g")
        self.item = item
        self.rep = rep or Skip()
        self.width = max(self.item.width, self.rep.width) + Options.AR * 2
        self.height = self.item.height
        self.up = self.item.up
        self.down = max(Options.AR * 2, self.item.down + Options.VS + self.rep.up
                        + self.rep.height + self.rep.down)
        self.needs_space = True

    def format(self, x, y, width):
        gaps = determine_gaps(width, self.width)
        Path(x, y).h(gaps[0]).add_to(self)
        Path(x + gaps[0] + self.width, y + self.height).h(gaps[1]).add_to(self)
        x += gaps[0]
        Path(x, y).right(Options.AR).add_to(self)
        self.item.format(x + Options.AR, y, self.width - Options.AR * 2).add_to(self)
        Path(x + self.width - Options.AR, y + self.height).right(Options.AR).add_to(self)
        distance = max(Options.AR * 2, self.item.height + self.item.down + Options.VS + self.rep.up)
        Path(x + Options.AR, y).arc("nw").down(distance - Options.AR * 2).arc("ws").add_to(self)
        self.rep.format(x + Options.AR, y + distance, self.width - Options.AR * 2).add_to(self)
        (Path(x + self.width - Options.AR, y + distance + self.rep.height)
            .arc("se").up(distance - Options.AR * 2 + self.rep.height - self.item.height)
            .arc("en").add_to(self))
        return self


def ZeroOrMore(item):
    return Optional(OneOrMore(item))


class _Box(FakeSVG):
    """Terminal and NonTerminal."""

    def __init__(self, kind, text, rounded):
        super().__init__("g", {"class": kind + " "})
        self.text = str(text)
        self.rounded = rounded
        self.width = len(self.text) * Options.CHAR_WIDTH + 20
        self.height = 0
        self.up = 11
        self.down = 11
        self.needs_space = True

    def format(self, x, y, width):
        gaps = determine_gaps(width, self.width)
        Path(x, y).h(gaps[0]).add_to(self)
        Path(x + gaps[0] + self.width, y).h(gaps[1]).add_to(self)
        x += gaps[0]
        rect = {"x": x, "y": y - 11, "width": self.width, "height": self.up + self.down}
        if self.rounded:
            rect.update({"rx": 10, "ry": 10})
        FakeSVG("rect", rect).add_to(self)
        FakeSVG("text", {"x": x + self.width / 2, "y": y + 4}, self.text).add_to(self)
        return self


def Terminal(text):
    return _Box("terminal", text, False)


def NonTerminal(text):
    return _Box("non-terminal", text, True)


class Comment(FakeSVG):
    def __init__(self, text, cls=""):
        super().__init__("g", {"class": "comment " + cls})
        self.text = str(text)
        self.width = len(self.text) * Options.COMMENT_CHAR_WIDTH + 10
        self.height = 0
        self.up = 8
        self.down = 8
        self.needs_space = True

    def format(self, x, y, width):
        gaps = determine_gaps(width, self.width)
        Path(x, y).h(gaps[0]).add_to(self)
        Path(x + gaps[0] + self.width, y + self.height).h(gaps[1]).add_to(self)
        x += gaps[0]
        FakeSVG("text", {"x": x + self.width / 2, "y": y + 5, "class": "comment"}, self.text).add_to(self)
        return self


# -- Reading MRSParser.g4 ------------------------------------------------------

_TOKEN_PATTERN = re.compile(
    r"""
    (?P<ws>\s+)
    | (?P<comment>//[^\n]*|/\*.*?\*/)
    | (?P<string>'(?:\\.|[^'\\])*')
    | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<punct>\+=|[:;|()?*+=~.#<>,])
    """,
    re.VERBOSE | re.DOTALL,
)


def tokenize(text):
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos] == "{":
            # An action or predicate block, with nested braces
            depth = 0
            start = pos
            while pos < len(text):
                if text[pos] == "{":
                    depth += 1
                elif text[pos] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1
            pos += 1
            tokens.append(("action", text[start:pos]))
            continue
        match = _TOKEN_PATTERN.match(text, pos)
        if not match:
            raise SyntaxError(f"Unexpected character {text[pos]!r} in the grammar at offset {pos}")
        pos = match.end()
        kind = match.lastgroup
        if kind in ("ws", "comment"):
            continue
        tokens.append((kind, match.group()))
    return tokens


class Grammar:
    """The parser rules of an ANTLR grammar as small parse trees:

    alternatives = [alternative, ...]
    alternative  = [element, ...]
    element      = (kind, value, suffix) with kind one of
                   "token" (TOKEN_REF), "rule" (RULE_REF), "string" (literal),
                   "any" (.), "block" (alternatives), "predicate", "action"
    """

    def __init__(self, text):
        self.tokens = tokenize(text)
        self.pos = 0
        self.rules = {}
        self._parse_grammar()

    def _peek(self, offset=0):
        index = self.pos + offset
        return self.tokens[index] if index < len(self.tokens) else (None, None)

    def _next(self):
        token = self._peek()
        self.pos += 1
        return token

    def _expect(self, value):
        token = self._next()
        if token[1] != value:
            raise SyntaxError(f"Expected {value!r}, found {token[1]!r}")

    def _parse_grammar(self):
        while self._peek()[0] is not None:
            kind, value = self._peek()
            if kind == "id" and value[0].islower() and self._peek(1)[1] == ":":
                self._parse_rule()
            elif kind == "id" and value in ("options", "tokens", "channels") and self._peek(1)[0] == "action":
                self.pos += 2
            else:
                # grammar declaration, named actions (@header {...}), ...
                self._next()

    def _parse_rule(self):
        name = self._next()[1]
        self._expect(":")
        self.rules[name] = self._parse_alternatives()
        self._expect(";")

    def _parse_alternatives(self):
        alternatives = [self._parse_alternative()]
        while self._peek()[1] == "|":
            self._next()
            alternatives.append(self._parse_alternative())
        return alternatives

    def _parse_alternative(self):
        elements = []
        while self._peek()[1] not in ("|", ")", ";", "#", None):
            elements.append(self._parse_element())
        if self._peek()[1] == "#":  # alternative label
            self.pos += 2
        return elements

    def _parse_suffix(self):
        suffix = ""
        if self._peek()[1] in ("?", "*", "+"):
            suffix = self._next()[1]
            if self._peek()[1] == "?":  # non-greedy
                suffix += self._next()[1]
        return suffix

    def _parse_element(self):
        kind, value = self._peek()
        if kind == "action":
            self._next()
            if self._peek()[1] == "?":
                self._next()
                return ("predicate", value, "")
            return ("action", value, "")
        if value == "(":
            self._next()
            alternatives = self._parse_alternatives()
            self._expect(")")
            return ("block", alternatives, self._parse_suffix())
        if kind == "id" and self._peek(1)[1] in ("=", "+="):
            # A label: the labeled atom or block is what is drawn
            self.pos += 2
            return self._parse_element()
        if value == "~":
            raise SyntaxError("Not-sets (~) are not supported by this renderer")
        self._next()
        if kind == "string":
            atom = ("string", value)
        elif value == ".":
            atom = ("any", value)
        elif kind == "id" and value[0].isupper():
            atom = ("token", value)
        elif kind == "id":
            atom = ("rule", value)
        else:
            raise SyntaxError(f"Unexpected {value!r} in the grammar")
        return (atom[0], atom[1], self._parse_suffix())


# -- The diagram generator of the VS Code extension (SVGGenerator) --------------


class DiagramGenerator:
    """Builds the railroad diagram of a rule the way the extension does,
    including its line wrapping after wrap_after characters."""

    def __init__(self, grammar, strip_pattern, wrap_after):
        self.grammar = grammar
        self.strip_pattern = strip_pattern
        self.wrap_after = wrap_after or 1e6
        self.nested_char_length = 0
        self.is_wrapped = False

    def generate(self, rule):
        self.nested_char_length = 0
        self.is_wrapped = False
        return self._alternatives(self.grammar.rules[rule])

    def _alternatives(self, alternatives):
        items = []
        max_child_length = 0
        for alternative in alternatives:
            items.append(self._alternative(alternative))
            max_child_length = max(max_child_length, self.nested_char_length)
        self.nested_char_length = max_child_length
        return ("Choice", items)

    def _alternative(self, elements):
        # Sequences of the items, each a line of the stack
        lines = [[]]
        current_length = 0
        max_child_length = 0
        for index, element in enumerate(elements):
            item = self._element(element)
            if current_length > max_child_length:
                max_child_length = current_length
            current_length += self.nested_char_length
            if index > 0:
                if current_length > self.wrap_after:
                    self.is_wrapped = True
                    lines.append([])
                    current_length = self.nested_char_length
            lines[-1].append(item)
        self.nested_char_length = max(max_child_length, current_length)
        if not elements:
            lines = [[("Comment", "<empty alt>", "rrd-warning")]]
        return ("Stack", [("Sequence", line) for line in lines])

    def _element(self, element):
        kind, value, suffix = element
        if kind == "predicate":
            return ("Comment", value + "?", "rrd-predicate")
        if kind == "action":
            return ("Comment", "{ action code }", "")
        if kind == "block":
            item = self._alternatives(value)
        elif kind == "any":
            item = ("NonTerminal", "any token")
        else:
            item = self._terminal(kind, value)
        if suffix == "?":
            return ("Optional", item)
        if suffix == "*":
            return ("ZeroOrMore", item)
        if suffix:  # "+" and the non-greedy forms
            return ("OneOrMore", item)
        return item

    def _terminal(self, kind, text):
        if kind == "string":
            # The extension escapes literals for its JavaScript code; the
            # length it wraps by includes the escapes
            escaped = text.replace("\\", "\\\\").replace("'", "\\'")
            content = self.strip_pattern.sub("", escaped, count=1).replace("DATABASE", "SCHEMA", 1)
            self.nested_char_length = len(content)
            return ("Terminal", content.replace("\\'", "'").replace("\\\\", "\\"))
        content = self.strip_pattern.sub("", text, count=1).replace("DATABASE", "SCHEMA", 1)
        self.nested_char_length = len(content)
        return ("Terminal" if kind == "token" else "NonTerminal", content)


def build(node):
    """The railroad diagram objects of a generated description."""
    kind = node[0]
    if kind == "Choice":
        return Choice(0, *[build(item) for item in node[1]])
    if kind == "Stack":
        return Stack(*[build(item) for item in node[1]])
    if kind == "Sequence":
        return Sequence(*[build(item) for item in node[1]])
    if kind == "Optional":
        return Optional(build(node[1]))
    if kind == "ZeroOrMore":
        return ZeroOrMore(build(node[1]))
    if kind == "OneOrMore":
        return OneOrMore(build(node[1]))
    if kind == "Terminal":
        return Terminal(node[1])
    if kind == "NonTerminal":
        return NonTerminal(node[1])
    if kind == "Comment":
        return Comment(node[1], node[2])
    raise ValueError(kind)


# -- The drawn extent -------------------------------------------------------------

_PATH_COMMAND = re.compile(r"([MmHhVvAa])\s*([^MmHhVvAa]*)")


def drawn_extent(node, extent=None):
    """The largest x and y anything in the diagram is drawn at."""
    if extent is None:
        extent = [0.0, 0.0]
    if node.tag_name == "path":
        x = y = 0.0
        for command, args in _PATH_COMMAND.findall(node.attrs.get("d", "")):
            values = [float(v) for v in args.replace(",", " ").split()]
            if command == "M":
                x, y = values[0], values[1]
            elif command == "m":
                x, y = x + values[0], y + values[1]
            elif command in "Hh":
                x = values[0] if command == "H" else x + values[0]
            elif command in "Vv":
                y = values[0] if command == "V" else y + values[0]
            elif command in "Aa":
                x, y = (values[5], values[6]) if command == "A" else (x + values[5], y + values[6])
            extent[0] = max(extent[0], x)
            extent[1] = max(extent[1], y)
    elif node.tag_name == "rect":
        extent[0] = max(extent[0], node.attrs["x"] + node.attrs["width"])
        extent[1] = max(extent[1], node.attrs["y"] + node.attrs["height"])
    elif node.tag_name == "text":
        # The baseline is 4 px below the line; leave room for descenders
        extent[1] = max(extent[1], node.attrs["y"] + 4)
    if not isinstance(node.children, str):
        for child in node.children:
            drawn_extent(child, extent)
    return extent


# -- SVG output ---------------------------------------------------------------------


def _escape_attribute(value):
    return js_str(value).replace("&", "&amp;").replace('"', "&quot;").replace(" ", "&nbsp;")


def _escape_text(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace(" ", "&nbsp;"))


def outer_html(node, extra_attrs=None):
    """The markup a browser serializes the SVG element to."""
    attrs = dict(node.attrs)
    if extra_attrs:
        attrs.update(extra_attrs)
    result = "<" + node.tag_name
    for key, value in attrs.items():
        result += f' {key}="{_escape_attribute(value)}"'
    result += ">"
    if isinstance(node.children, str):
        result += _escape_text(node.children)
    else:
        result += "".join(outer_html(child) for child in node.children)
    return result + "</" + node.tag_name + ">"


def render(grammar, rule):
    generator = DiagramGenerator(grammar, STRIP_PATTERN, WRAP_AFTER)
    description = generator.generate(rule)

    Options.INTERNAL_ALIGNMENT = "left" if generator.is_wrapped else "center"
    diagram = Diagram(build(description), complex=True).format()

    # Cover everything drawn. The library computes the height of some
    # diagrams too small (loops and choices inside wrapped sequences), which
    # cut off or crowded their bottom; those get the regular 20 px padding
    # below the lowest drawn element. All other sizes stay as computed.
    padding = 20
    extent_x, extent_y = drawn_extent(diagram)
    width = diagram.attrs["width"]
    height = diagram.attrs["height"]
    if width - extent_x < padding / 2:
        width = math.ceil(extent_x + padding)
    if height - extent_y < padding / 2:
        height = math.ceil(extent_y + padding)
    if (width, height) != (diagram.attrs["width"], diagram.attrs["height"]):
        diagram.attrs["width"] = width
        diagram.attrs["height"] = height
        diagram.attrs["viewBox"] = f"0 0 {js_num(width)} {js_num(height)}"

    svg = ('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
           '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
           '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n')
    svg += outer_html(diagram, {
        "id": rule,
        "xmlns": "http://www.w3.org/2000/svg",
        "xmlns:xlink": "http://www.w3.org/1999/xlink",
    })
    return svg.replace(
        "</svg>",
        f'<defs><style type="text/css"><![CDATA[\n{STYLES}\n ]]>\n</style></defs></svg>')


def main():
    parser = argparse.ArgumentParser(description="Render the railroad diagrams of MRSParser.g4.")
    parser.add_argument("rules", nargs="*", help="only render these rules")
    parser.add_argument("--check", action="store_true", help="report changes, write nothing")
    parser.add_argument("--prune", action="store_true",
                        help="delete SVG files of rules that no longer exist")
    parser.add_argument("--grammar", default=GRAMMAR_FILE, help="the parser grammar")
    parser.add_argument("--output", default=OUTPUT_DIR, help="the output folder")
    args = parser.parse_args()

    with open(args.grammar, encoding="utf-8") as f:
        grammar = Grammar(f.read())

    rules = args.rules or list(grammar.rules)
    unknown = [rule for rule in rules if rule not in grammar.rules]
    if unknown:
        sys.exit(f"Unknown rules: {', '.join(unknown)}")

    os.makedirs(args.output, exist_ok=True)
    changed = []
    for rule in rules:
        svg = render(grammar, rule)
        path = os.path.join(args.output, rule + ".svg")
        try:
            with open(path, encoding="utf-8") as f:
                if f.read() == svg:
                    continue
        except FileNotFoundError:
            pass
        changed.append(rule)
        if not args.check:
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)

    stale = sorted(f[:-4] for f in os.listdir(args.output)
                   if f.endswith(".svg") and f[:-4] not in grammar.rules)

    verb = "would change" if args.check else "written"
    print(f"{len(rules)} rules rendered, {len(changed)} files {verb}"
          + (": " + ", ".join(changed) if changed else ""))
    if stale:
        if args.prune and not args.check:
            for name in stale:
                os.remove(os.path.join(args.output, name + ".svg"))
            print("Deleted the files of rules that no longer exist: " + ", ".join(stale))
        else:
            print("Files of rules that no longer exist (--prune deletes them): " + ", ".join(stale))
    if args.check and changed:
        sys.exit(1)


if __name__ == "__main__":
    main()
