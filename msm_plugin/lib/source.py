# Copyright (c) 2025, 2026, Oracle and/or its affiliates.
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

"""MSM SOURCE directives: the one implementation of what they mean.

A schema project's development script pulls other files in with
``SOURCE '<path>'[<start>:<end>]; -- comment`` lines, which are replaced by
the selected part of the file before the script is used. msm_plugin's
prepare_release expands them, and so does mcp_plugin's db.execute_sql_script,
both through :func:`expand_source_directives`; the language server
(mariadb-corporation/lsp-server, docs/plans/source-includes.md) follows the
same rules.

This module needs nothing from the shell, so it can be imported wherever the
text has to be expanded.
"""

import bisect
import os
import re
from dataclasses import dataclass
from textwrap import indent
from typing import Callable, Optional

# SOURCE '<path>'[<start>:<end>]; -- comment
# The slice is optional (the whole file without it). A bound is empty, a
# character offset (Python slice semantics) or a line number lnN (1-based, the
# end line included, ln-1 is the last line). The path ends at a matching quote
# on the directive's line. Matched with re.ASCII, so \s and \d are ASCII only,
# as the language server's scan (mariadb-lsp src/source_include.cc) takes them.
MSM_SECTION_SOURCE_REGEX = (
    r'^([ \t]*)SOURCE\s+([\'"])([^\n]*?)\2'
    r"\s*(?:\[\s*((?:ln)?-?\d*)\s*:\s*((?:ln)?-?\d*)\s*\])?\s*;[^\n]*(?:\n|\Z)"
)

_SOURCE_FLAGS = re.MULTILINE | re.ASCII


class SourceError(ValueError):
    """A SOURCE directive that cannot be expanded.

    Attributes:
        line (int): The line of the directive in the script, from 1.
        path (str): The path as the directive writes it.
        reason (str): What is wrong with it.
    """

    def __init__(self, line: int, path: str, reason: str):
        super().__init__(f"SOURCE '{path}' on line {line}: {reason}")
        self.line = line
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class Segment:
    """A stretch of the expanded text, and where it came from.

    It ends where the next one starts, or with the text.

    Attributes:
        start (int): Where it starts in the expanded text.
        file (str): The included file it came from, or None for the script.
        origin (int): For the script, the offset in the script the segment
            starts at; for an included file, the offset of the SOURCE keyword
            of the directive that included it.
        first_line (int): For an included file, the file's line the segment
            starts on, from 1; 0 for the script.
        first_column (int): For an included file, where on that line the
            selected part starts, in characters from 0.
        indent (int): The directive's indentation, which every included
            line that is not blank was given.
    """

    start: int
    file: Optional[str]
    origin: int
    first_line: int = 0
    first_column: int = 0
    indent: int = 0


def get_source_slice_offset(content: str, bound: str, is_end: bool) -> int | None:
    """Returns the character offset a bound of a SOURCE slice stands for

    Args:
        content: The content of the referenced file
        bound: The bound as written: "", a character offset or lnN
        is_end: Whether it is the end bound (the line it names is included)

    Returns:
        The character offset, or None for an empty bound
    """
    if bound == "":
        return None
    is_line = bound.startswith("ln")
    number = bound[2:] if is_line else bound
    if number in ("", "-") or (is_line and int(number) == 0):
        raise ValueError(
            f"The SOURCE slice bound `{bound}` is neither a character offset nor a "
            "line number (lines count from 1)."
        )
    if not is_line:
        return int(number)

    line = int(number)
    # Lines end at "\n" only (reading in text mode made "\r\n" one), as an
    # editor counts them; str.splitlines() would also break at "\f" and others
    line_starts = [0] + [m.end() for m in re.finditer("\n", content)]
    if line_starts[-1] != len(content):
        line_starts.append(len(content))
    line_count = len(line_starts) - 1

    # The index of the first line taken, or of the first line after the slice
    if is_end:
        index = line if line > 0 else line_count + line + 1
    else:
        index = line - 1 if line > 0 else line_count + line
    return line_starts[max(0, min(index, line_count))]


def get_source_slice(content: str, start: str, end: str) -> str:
    """Returns the part of a referenced file a SOURCE slice selects

    Args:
        content: The content of the referenced file
        start: The start bound as written: "", a character offset or lnN
        end: The end bound as written: "", a character offset or lnN

    Returns:
        The selected part of the content
    """
    return content[
        get_source_slice_offset(content, start, False) : get_source_slice_offset(
            content, end, True
        )
    ]


def resolve_include_path(path: str, base_dir: Optional[str]) -> str:
    """Returns the file a SOURCE directive's path names.

    An absolute path as it is, a relative one joined to base_dir; ``..`` is
    folded, symlinks are not followed.

    Args:
        path: The path as the directive writes it.
        base_dir: The folder relative paths are taken from, or None.

    Returns:
        The path of the file.

    Raises:
        ValueError: For a relative path without a base_dir.
    """
    if not os.path.isabs(path):
        if base_dir is None:
            raise ValueError(
                "a relative path, and no folder to take it from."
            )
        path = os.path.join(base_dir, path)
    return os.path.normpath(path)


def read_include(path: str) -> tuple[str, str]:
    """Reads a file a SOURCE directive names, as msm does.

    Args:
        path: The file, resolved by :func:`resolve_include_path`.

    Returns:
        The path of the file read and its content.
    """
    if not os.path.exists(path):
        raise ValueError(f"The file `{path}` could not be resolved.")
    with open(path, "r") as f:
        return path, f.read()


def contains_source_directive(text: str) -> bool:
    """Returns whether the text holds a SOURCE directive.

    Args:
        text: The text to look at.

    Returns:
        True if a line of it is a SOURCE directive.
    """
    return re.search(MSM_SECTION_SOURCE_REGEX, text, _SOURCE_FLAGS) is not None


def _directive_matches(script: str):
    """Yields each SOURCE directive of a script with the line it is on."""
    line = 1
    position = 0
    for match in re.finditer(MSM_SECTION_SOURCE_REGEX, script, _SOURCE_FLAGS):
        line += script.count("\n", position, match.start())
        position = match.start()
        yield line, match


def directives(script: str) -> list[tuple[int, str]]:
    """Lists the SOURCE directives of a script, without reading anything.

    Args:
        script: The script.

    Returns:
        One (line, path as written) per directive, in order; lines from 1.
    """
    return [(line, match.group(3)) for line, match in _directive_matches(script)]


def expand_source_directives(
    script: str,
    base_dir: Optional[str],
    read: Callable[[str], tuple[str, str]] = read_include,
    allow_nested: bool = True,
) -> tuple[str, list[Segment]]:
    """Replaces every SOURCE directive of a script by the part it selects.

    The directive's line - its trailing comment and line break with it - is
    replaced by the selected part of the file, indented as the directive is.
    One level only: a SOURCE line in an included file is not expanded.

    Args:
        script: The script.
        base_dir: The folder relative paths are taken from.
        read: Reads a file a directive names: given its path, resolved by
            :func:`resolve_include_path`, returns the path it read and the
            content. The default reads it as msm does; a caller that has to
            check the path passes its own and raises a ValueError (or
            OSError) to refuse it.
        allow_nested: Whether a SOURCE line may be in an included part, where
            it is left as it is. False refuses it, for a caller that runs the
            text: the line would fail halfway through.

    Returns:
        The expanded script, and its segments in order: which stretch came
        from the script and which from which file.

    Raises:
        SourceError: For a directive that cannot be expanded, naming its line
            and its path.
    """
    parts = []
    segments = []
    expanded_length = 0
    position = 0

    def add(text, file, origin, first_line=0, first_column=0, indented=0):
        nonlocal expanded_length
        if text:
            parts.append(text)
            segments.append(
                Segment(
                    expanded_length, file, origin, first_line, first_column, indented
                )
            )
            expanded_length += len(text)

    for line, match in _directive_matches(script):
        add(script[position : match.start()], None, position)

        source_indention = len(match.group(1))
        path = match.group(3)
        slice_start = match.group(4) or ""
        slice_end = match.group(5) or ""

        try:
            read_path, content = read(resolve_include_path(path, base_dir))
            start = get_source_slice_offset(content, slice_start, False)
            selected = content[
                start : get_source_slice_offset(content, slice_end, True)
            ]
        except (OSError, ValueError) as error:
            raise SourceError(line, path, str(error)) from error

        if not allow_nested and contains_source_directive(selected):
            raise SourceError(
                line,
                path,
                f"`{read_path}` holds a SOURCE directive itself, and SOURCE "
                "is expanded one level only.",
            )

        if source_indention > 0:
            selected = indent(selected, source_indention * " ")
        # Placed at the SOURCE keyword, where a splitter's piece starts, and
        # at the file's line and column the selected part starts on.
        begin = slice(start, None).indices(len(content))[0]
        add(
            selected,
            read_path,
            match.end(1),
            content.count("\n", 0, begin) + 1,
            begin - (content.rfind("\n", 0, begin) + 1),
            source_indention,
        )
        position = match.end()

    add(script[position:], None, position)

    return "".join(parts), segments


def locate(
    expanded: str, segments: list[Segment], offset: int
) -> tuple[int, Optional[str], int, int]:
    """Says where a place in an expanded script was written.

    Args:
        expanded: The expanded script.
        segments: Its segments, from :func:`expand_source_directives`.
        offset: The place in the expanded script.

    Returns:
        The offset in the script as given - for a place in an included part,
        the one of the directive's SOURCE keyword - then the included file
        and its line and column (both from 1, the column in characters), or
        None, 0 and 0 for a place in the script.
    """
    index = bisect.bisect_right([segment.start for segment in segments], offset)
    segment = segments[max(0, index - 1)]
    if segment.file is None:
        return segment.origin + (offset - segment.start), None, 0, 0

    # Indenting the part added no line breaks, so they count as in the file.
    line = segment.first_line + expanded.count("\n", segment.start, offset)
    line_start = expanded.rfind("\n", 0, offset) + 1
    line_end = expanded.find("\n", offset)
    text = expanded[line_start : len(expanded) if line_end < 0 else line_end]
    # The indentation the expansion added, on a line that is not blank.
    added = segment.indent if text.strip() else 0
    if line_start <= segment.start:
        column = segment.first_column + max(0, offset - segment.start - added)
    else:
        column = max(0, offset - line_start - added)
    return segment.origin, segment.file, line, column + 1
