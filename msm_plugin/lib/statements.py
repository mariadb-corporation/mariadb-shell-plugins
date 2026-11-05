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

"""The statements of a development script's sections, and what each creates.

A section is cut into its statements at the offsets the mariadb-lsp binary's
`split` command gives (lib/lsp.py), the splitter MariaDB Shell runs scripts
with, so DELIMITER and sql_mode changes are followed as the server reads
them. Every character of the section stays somewhere: a statement takes the
comments before it and one after it on its line, the text above the first
statement's comments is the section's preamble, the text after the last
statement its trailer. Only DELIMITER lines are dropped, since every block
of statements written back gets a DELIMITER block of its own.

No shell imports: lib/workbench.py classifies the SQL of a model with it.
"""

import re
from dataclasses import dataclass
from typing import Optional

_CREATE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM\s*=\s*\w+\s+)?"
    r"(?:DEFINER\s*=\s*\S+\s+)?(?:SQL\s+SECURITY\s+\w+\s+)?"
    r"(?:AGGREGATE\s+)?(?P<kind>VIEW|PROCEDURE|FUNCTION|TRIGGER|EVENT)\b",
    re.IGNORECASE,
)
_DROP = re.compile(
    r"^\s*DROP\s+(?P<kind>VIEW|PROCEDURE|FUNCTION|TRIGGER|EVENT)\b", re.IGNORECASE
)
_TABLE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?(?:TABLE|SEQUENCE)\b", re.IGNORECASE
)
_TABLE_ADDITION = re.compile(
    r"^\s*(?:ALTER\s+(?:ONLINE\s+)?(?:IGNORE\s+)?(?:TABLE|SEQUENCE)"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?(?:ONLINE\s+|OFFLINE\s+)?"
    r"(?:UNIQUE\s+|FULLTEXT\s+|SPATIAL\s+)?INDEX)\b",
    re.IGNORECASE,
)
_INSERT = re.compile(r"^\s*(?:INSERT|REPLACE)\b", re.IGNORECASE)
_DELIMITER_LINE = re.compile(r"^[ \t]*DELIMITER[ \t]+\S+[ \t]*(?:\n|$)", re.IGNORECASE | re.MULTILINE)


def strip_statement(sql: str) -> str:
    """A statement without the DELIMITER lines, the comments before it
    (Workbench puts `-- View structure for view ...` there) and the
    delimiter at its end."""
    lines = [line for line in sql.strip().splitlines()
             if not line.strip().upper().startswith("DELIMITER")]
    text = "\n".join(lines).strip()
    while True:
        if text.startswith("--") or text.startswith("#"):
            text = text.partition("\n")[2]
        elif text.startswith("/*") and not text.startswith("/*!"):
            text = text.partition("*/")[2]
        else:
            break
        text = text.lstrip()
    for delimiter in ("$$", "%%", "//", ";"):
        if text.endswith(delimiter):
            text = text[: -len(delimiter)].rstrip()
            break
    return text


def first_line(text: str) -> str:
    """The first line of a statement's SQL, without the comments above it."""
    lines = [line for line in strip_statement(text).splitlines() if line.strip()]
    return lines[0].strip() if lines else ""


def statement_kind(sql: str) -> Optional[str]:
    """VIEW, PROCEDURE, FUNCTION, TRIGGER or EVENT: what a CREATE statement creates."""
    match = _CREATE.match(strip_statement(sql))
    return match.group("kind").upper() if match else None


def object_kind(sql: str) -> Optional[str]:
    """What a statement of a development script is about, as its sections group them.

    TABLE (CREATE TABLE or SEQUENCE), TABLE_ADDITION (ALTER TABLE or SEQUENCE,
    CREATE INDEX), INSERT (INSERT, REPLACE), and VIEW, PROCEDURE, FUNCTION,
    TRIGGER or EVENT for a CREATE or DROP of one; None for anything else.
    """
    text = strip_statement(sql)
    match = _CREATE.match(text) or _DROP.match(text)
    if match:
        return match.group("kind").upper()
    if _TABLE.match(text):
        return "TABLE"
    if _TABLE_ADDITION.match(text):
        return "TABLE_ADDITION"
    if _INSERT.match(text):
        return "INSERT"
    return None


def without_delimiter_lines(text: str) -> str:
    """The text without its DELIMITER lines."""
    return _DELIMITER_LINE.sub("", text)


@dataclass
class Statement:
    """A statement of a section, with the text that goes where it goes.

    Attributes:
        text: The comments before it, the statement with its delimiter, and a
            comment after it on the same line, as written.
        delimiter: The delimiter in force, e.g. ; or %%.
        kind: What object_kind says it is about.
        index: Its place in the section.
        separator: The line breaks between it and the statement before it,
            kept where the two stay next to each other.
    """

    text: str
    delimiter: str
    kind: Optional[str]
    index: int = 0
    separator: str = "\n\n"


@dataclass
class SectionStatements:
    """A section body cut into statements.

    Attributes:
        preamble: The text before the first statement and its comments.
        statements: The Statements, in order.
        trailer: The text after the last statement.
    """

    preamble: str
    statements: list
    trailer: str


def char_offsets(text: str, byte_offsets: list) -> list:
    """The character offsets of UTF-8 byte offsets into text."""
    encoded = text.encode("utf-8")
    order = sorted(set(byte_offsets))
    found = {}
    chars = previous = 0
    for offset in order:
        chars += len(encoded[previous:offset].decode("utf-8"))
        found[offset] = chars
        previous = offset
    return [found[offset] for offset in byte_offsets]


def _is_comment_line(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or stripped.startswith("--") or stripped.startswith("#")


def _split_preamble(gap: str) -> tuple:
    """The text before a section's first statement: (preamble, its own comments).

    The statement's comments are the run of line comments (and blank lines)
    right above it; a block comment, like the templates' commented-out
    examples, ends the run.
    """
    lines = gap.split("\n")
    start = len(lines)
    while start > 0 and _is_comment_line(lines[start - 1]):
        start -= 1
    while start < len(lines) and lines[start].strip() == "":
        start += 1
    return "\n".join(lines[:start]), "\n".join(lines[start:])


def _comment_after(text: str, end: int) -> int:
    """The end of a line comment right after a statement on its line, else end."""
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    rest = text[end:line_end].strip()
    if rest.startswith("--") or rest.startswith("#"):
        return line_end
    return end


def sql_entries(text: str, split: list) -> list:
    """The (begin, end, delimiter) of every statement `mariadb-lsp split` found in text.

    Args:
        text: The text given to `mariadb-lsp split`.
        split: Its statements (`split --format json`): kind ("sql" or
            "comment"), begin and end (UTF-8 bytes, end after the delimiter)
            and delimiter.

    Returns:
        One tuple per sql statement, in character offsets; comment-only
        entries are left out.
    """
    entries = [entry for entry in split if entry.get("kind") == "sql"]
    offsets = char_offsets(text, [offset for e in entries for offset in (e["begin"], e["end"])])
    return [
        (offsets[2 * index], offsets[2 * index + 1], entry.get("delimiter") or ";")
        for index, entry in enumerate(entries)
    ]


def cut_section(text: str, body_start: int, body_end: int, entries: list) -> SectionStatements:
    """Cuts the body of a section into statements.

    Args:
        text: The whole development script.
        body_start: Where the section's body starts in text (characters).
        body_end: Where it ends.
        entries: The script's statements, from sql_entries.

    Returns:
        The SectionStatements.

    Raises:
        ValueError: When a statement of the section runs past its end: the
            last one lacks its delimiter, so the splitter read the next
            section as part of it.
    """
    statements = []
    preamble = ""
    position = body_start
    for begin, end, delimiter in entries:
        if begin < body_start or begin >= body_end:
            continue
        if end > body_end:
            raise ValueError(
                "A statement runs past the end of the section, so it lacks its "
                f"delimiter `{delimiter}`: {first_line(text[begin:body_end])}"
            )
        end = min(_comment_after(text, end), body_end)
        gap = without_delimiter_lines(text[position:begin])
        separator = Statement.separator
        if not statements:
            preamble, gap = _split_preamble(gap)
        else:
            stripped = gap.lstrip("\n")
            separator = gap[: len(gap) - len(stripped)] or "\n"
            gap = stripped
        statement = text[begin:end]
        statements.append(Statement(
            text=(gap + statement).lstrip().rstrip("\n"),
            delimiter=delimiter,
            kind=object_kind(statement),
            index=len(statements),
            separator=separator,
        ))
        position = end
    trailer = without_delimiter_lines(text[position:body_end])
    if not statements:
        preamble, trailer = without_delimiter_lines(text[body_start:body_end]), ""
    return SectionStatements(preamble=preamble.strip("\n"), statements=statements,
                             trailer=trailer.strip("\n"))


def render(statements: list) -> str:
    """The SQL of statements, each run with a delimiter other than ; in a
    DELIMITER block of its own, so the text runs the same wherever it goes."""
    blocks = []
    run = []
    for statement in statements:
        if run and run[-1].delimiter != statement.delimiter:
            blocks.append(_block(run))
            run = []
        run.append(statement)
    if run:
        blocks.append(_block(run))
    return "\n\n".join(blocks)


def _block(run: list) -> str:
    sql = run[0].text
    for previous, statement in zip(run, run[1:]):
        together = statement.index == previous.index + 1
        sql += (statement.separator if together else "\n\n") + statement.text
    delimiter = run[0].delimiter
    if delimiter == ";":
        return sql
    return f"DELIMITER {delimiter}\n\n{sql}\n\nDELIMITER ;"
