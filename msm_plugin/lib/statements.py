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
from dataclasses import dataclass, field
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
# A DROP of a table or an object, for dropped_kind: object_kind leaves a
# table's DROP out of the kinds the sections group.
_DROP_ANY = re.compile(
    r"^\s*DROP\s+(?P<kind>TABLE|SEQUENCE|VIEW|PROCEDURE|FUNCTION|TRIGGER|EVENT)\b",
    re.IGNORECASE,
)
_TABLE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+)?(?:TABLE|SEQUENCE)\b", re.IGNORECASE
)
_CREATE_INDEX = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:ONLINE\s+|OFFLINE\s+)?"
    r"(?:UNIQUE\s+|FULLTEXT\s+|SPATIAL\s+)?INDEX\b",
    re.IGNORECASE,
)
# A name, quoted or not
_NAME = r"`(?:[^`]|``)*`|[\w$]+"
_ALTER_TABLE = re.compile(
    r"^\s*ALTER\s+(?:ONLINE\s+)?(?:IGNORE\s+)?TABLE\s+(?:IF\s+EXISTS\s+)?"
    rf"(?:{_NAME})(?:\s*\.\s*(?:{_NAME}))?\s*",
    re.IGNORECASE,
)
# What an ALTER TABLE of Section 140's additions may do: add a key, an index
# or a constraint. These keep their meaning when they run once all TABLEs
# and before the INSERTs; a column added, changed or dropped, or a rename,
# would not (a positional INSERT, a CREATE TABLE ... AS SELECT).
_ADDITION_CLAUSE = re.compile(
    r"^ADD\s+(?:CONSTRAINT\b|FOREIGN\s+KEY\b|PRIMARY\s+KEY\b|CHECK\b"
    r"|(?:UNIQUE|FULLTEXT|SPATIAL)\b|(?:INDEX|KEY)\b)",
    re.IGNORECASE,
)
_ALTER_TOKEN = re.compile(
    r"`(?:[^`]|``)*`|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\""
    r"|--[^\n]*|#[^\n]*|/\*.*?\*/|[(),]|[^`'\"(),#/-]+|.",
    re.DOTALL,
)
_INSERT = re.compile(r"^\s*(?:INSERT|REPLACE)\b", re.IGNORECASE)
# A comment after the new delimiter goes with the line: the client reads the
# delimiter up to the first blank.
_DELIMITER_LINE = re.compile(r"^[ \t]*DELIMITER[ \t]+\S+[^\n]*(?:\n|$)", re.IGNORECASE | re.MULTILINE)


def quote_identifier(name: str) -> str:
    """A name in backticks."""
    return "`" + name.replace("`", "``") + "`"


def _is_comment(token: str) -> bool:
    """Whether a token is a comment (an executable /*! ... */ is none)."""
    return token.startswith(("--", "#")) or (token.startswith("/*") and not token.startswith("/*!"))


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


def _alter_clauses(text: str) -> list:
    """The clauses of an ALTER TABLE after its table name, split at the
    commas outside quotes, comments and parentheses; comments left out."""
    clauses, current, depth = [], [], 0
    for token in _ALTER_TOKEN.findall(text):
        if _is_comment(token):
            current.append(" ")
            continue
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token == "," and depth == 0:
            clauses.append("".join(current).strip())
            current = []
            continue
        current.append(token)
    clauses.append("".join(current).strip())
    return clauses


# A clause of an ALTER TABLE that decides how the server runs it, also
# after a table option without a comma (ENGINE=InnoDB LOCK=NONE). LOCK is
# reserved, so outside quotes it is the clause; algorithm may name a column,
# so it counts only before = or one of its values.
_LOCKING_CLAUSE = re.compile(
    r"\bLOCK\b\s*(?:=\s*)?\w*"
    r"|\bALGORITHM\b\s*(?:=\s*\w*|(?:DEFAULT|INSTANT|NOCOPY|INPLACE|COPY)\b)",
    re.IGNORECASE,
)


# PARTITION is reserved, so outside quotes the word is the partitioning
_PARTITION_WORD = re.compile(r"\bPARTITION(?:S|ING)?\b", re.IGNORECASE)


def changes_partitioning(spec: str) -> bool:
    """Whether an ALTER TABLE's clauses (the text after its table name)
    touch its partitioning (PARTITION BY, ADD/DROP/... PARTITION, REMOVE
    PARTITIONING): the server takes no ALGORITHM or LOCK clause after those.
    Quotes and comments do not count."""
    return any(
        _PARTITION_WORD.search(token)
        for token in _ALTER_TOKEN.findall(spec)
        if not token.startswith(("`", "'", '"')) and not _is_comment(token)
    )


def ends_in_line_comment(text: str) -> bool:
    """Whether text ends in a -- or # comment, which would swallow what is
    appended on the same line."""
    tokens = [token for token in _ALTER_TOKEN.findall(text) if not token.isspace()]
    return bool(tokens) and tokens[-1].startswith(("--", "#"))


def locking_clause(spec: str) -> Optional[str]:
    """The first ALGORITHM or LOCK clause of an ALTER TABLE's clauses (the
    text after its table name), as written; None where it has none. Quotes
    and parentheses (a column's definition) do not count; a comment counts
    as a blank, so ALGORITHM /* ... */ = COPY is still the clause."""
    parts, depth = [], 0
    for token in _ALTER_TOKEN.findall(spec):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0 and _is_comment(token):
            parts.append(" ")
            continue
        elif depth == 0 and not token.startswith(("`", "'", '"')):
            parts.append(token)
            continue
        # A separator no clause spans
        parts.append("\0")
    text = "".join(parts)
    for match in _LOCKING_CLAUSE.finditer(text):
        # CHANGE [COLUMN] [IF EXISTS] algorithm copy INT renames a column
        before = text[:match.start()].split()
        if (match.group(0)[:1].upper() == "A" and "=" not in match.group(0)
                and before and before[-1].upper() in ("CHANGE", "COLUMN", "EXISTS")):
            continue
        return " ".join(match.group(0).split())
    return None


_ALTER_TABLE_PARTS = re.compile(
    r"^\s*ALTER\s+(?P<online>ONLINE\s+)?(?P<ignore>IGNORE\s+)?TABLE\s+"
    r"(?P<if_exists>IF\s+EXISTS\s+)?"
    rf"(?P<first>{_NAME})(?:\s*\.\s*(?P<second>{_NAME}))?\s*(?P<spec>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _unquote(name: str) -> str:
    if name.startswith("`"):
        return name[1:-1].replace("``", "`")
    return name


@dataclass
class AlterTable:
    """An ALTER TABLE statement taken apart.

    Attributes:
        schema: The schema it names; None where it names none.
        table: The table, unquoted.
        spec: Everything after the table name.
        modifiers: The words between ALTER and the name it has, upper case:
            ONLINE, IGNORE, IF EXISTS.
    """

    schema: Optional[str]
    table: str
    spec: str
    modifiers: list


def alter_table_parts(sql: str) -> Optional[AlterTable]:
    """An ALTER TABLE statement taken apart (comments before it and the
    delimiter after it allowed); None for any other statement."""
    match = _ALTER_TABLE_PARTS.match(strip_statement(sql))
    if not match:
        return None
    modifiers = [
        word for word, group in (("ONLINE", "online"), ("IGNORE", "ignore"),
                                 ("IF EXISTS", "if_exists"))
        if match.group(group)
    ]
    first, second = _unquote(match.group("first")), match.group("second")
    if second is None:
        return AlterTable(None, first, match.group("spec").strip(), modifiers)
    return AlterTable(first, _unquote(second), match.group("spec").strip(), modifiers)


# The tokens of a stored program's body, for top_level_statements: quoted
# names and strings, comments, words, and single characters.
_BODY_TOKEN = re.compile(
    r"`(?:[^`]|``)*`|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\""
    r"|--(?=\s|$)[^\n]*|#[^\n]*|/\*.*?\*/|[A-Za-z_][\w$]*|\s+|.",
    re.DOTALL,
)
# The words that open a compound statement where a statement starts, closed
# by END [<word>]
_OPENERS = ("BEGIN", "IF", "CASE", "LOOP", "WHILE", "REPEAT")
# The words after which a statement starts (and a label's colon, and ;)
_STATEMENT_BEFORE = ("BEGIN", "ATOMIC", "DO", "LOOP", "REPEAT")


def top_level_statements(body: str) -> list:
    """The statements of a stored program's body at its top level, each
    without its `;`: an IF ... END IF, a BEGIN ... END, a loop or a CASE
    statement stays one statement with everything in it.

    A compound statement opens only where a statement starts: IF(, REPEAT(
    and IF [NOT] EXISTS are no statements. A CASE anywhere else is an
    expression, which its own END closes; a THEN or ELSE inside it starts no
    statement.

    Raises:
        ValueError: Where the body's compound statements do not balance.
    """
    tokens = [
        (match.start(), match.group())
        for match in _BODY_TOKEN.finditer(body)
        if _is_significant(match.group())
    ]
    # What is open: "block" (a compound statement) or "expression" (CASE)
    statements_, stack, start, skip, previous = [], [], 0, False, None
    for index, (position, token) in enumerate(tokens):
        word = token.upper()
        if skip:
            skip, previous = False, word
            continue
        following = tokens[index + 1][1].upper() if index + 1 < len(tokens) else ""
        at_start = (
            previous in (None, ";", ":") + _STATEMENT_BEFORE
            or (previous in ("THEN", "ELSE") and (not stack or stack[-1] == "block"))
        )
        if word == "END":
            if not stack:
                raise ValueError("An END closes no compound statement.")
            # END IF, END LOOP, ... close one statement, as END does
            skip = stack.pop() == "block" and following in _OPENERS and following != "BEGIN"
        elif word in _OPENERS and at_start:
            stack.append("block")
        elif word == "CASE":
            stack.append("expression")
        elif token == ";" and not stack:
            text = body[start:position].strip()
            if _has_significant(text):
                statements_.append(text)
            start = position + 1
        previous = word
    if stack:
        raise ValueError("A compound statement is not closed.")
    text = body[start:].strip()
    if _has_significant(text):
        statements_.append(text)
    return statements_


def _is_significant(token: str) -> bool:
    return not token.isspace() and not _is_comment(token)


def _has_significant(text: str) -> bool:
    """Whether text holds more than blanks and comments."""
    return any(_is_significant(match.group()) for match in _BODY_TOKEN.finditer(text))


def _is_table_addition(text: str) -> bool:
    """CREATE INDEX, or an ALTER TABLE whose every clause adds a key, an
    index or a constraint (_ADDITION_CLAUSE)."""
    if _CREATE_INDEX.match(text):
        return True
    match = _ALTER_TABLE.match(text)
    if not match:
        return False
    clauses = _alter_clauses(text[match.end():])
    return all(_ADDITION_CLAUSE.match(clause) for clause in clauses)


def statement_kind(sql: str) -> Optional[str]:
    """VIEW, PROCEDURE, FUNCTION, TRIGGER or EVENT: what a CREATE statement creates."""
    match = _CREATE.match(strip_statement(sql))
    return match.group("kind").upper() if match else None


def dropped_kind(sql: str) -> Optional[str]:
    """TABLE, SEQUENCE, VIEW, PROCEDURE, FUNCTION, TRIGGER or EVENT: what a DROP statement drops."""
    match = _DROP_ANY.match(strip_statement(sql))
    return match.group("kind").upper() if match else None


_DROP_IF_EXISTS_NAME = re.compile(rf"\s*IF\s+EXISTS\s+(?P<name>{_NAME})\s*$", re.IGNORECASE)


def dropped_if_exists(sql: str) -> Optional[tuple]:
    """(kind, name unquoted) of a DROP ... IF EXISTS of one object named
    unqualified, as dropped_kind gives the kind; None for another statement."""
    stripped = strip_statement(sql)
    match = _DROP_ANY.match(stripped)
    rest = match and _DROP_IF_EXISTS_NAME.match(stripped, match.end())
    return (match.group("kind").upper(), _unquote(rest.group("name"))) if rest else None


def object_kind(sql: str) -> Optional[str]:
    """What a statement of a development script is about, as its sections group them.

    TABLE (CREATE TABLE or SEQUENCE), TABLE_ADDITION (CREATE INDEX, or an
    ALTER TABLE that only adds keys, indexes and constraints), INSERT (INSERT, REPLACE), and VIEW, PROCEDURE, FUNCTION,
    TRIGGER or EVENT for a CREATE or DROP of one; None for anything else.
    """
    text = strip_statement(sql)
    match = _CREATE.match(text) or _DROP.match(text)
    if match:
        return match.group("kind").upper()
    if _TABLE.match(text):
        return "TABLE"
    if _is_table_addition(text):
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
        ValueError: When a statement runs past the section's end (its last
            one lacks its delimiter, so the splitter read the next section as
            part of it), or one of the section before runs into it.
    """
    statements = []
    preamble = ""
    position = body_start
    for begin, end, delimiter in entries:
        if begin < body_start < end:
            raise ValueError(
                "A statement of the section before runs into this one, so it lacks its "
                f"delimiter `{delimiter}`: {first_line(text[begin:body_start])}"
            )
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


# -----------------------------------------------------------------------------
# Imported SQL, as the importers (workbench, live_schema) write it


@dataclass
class Imported:
    """The SQL of an imported schema (a Workbench model's, a live one's),
    grouped as an MSM development script keeps it.

    Each member is the SQL of one part, or None where it has none.
    """

    tables: Optional[str] = None
    table_additions: Optional[str] = None
    inserts: Optional[str] = None
    views: Optional[str] = None
    routines: Optional[str] = None
    triggers: Optional[str] = None
    events: Optional[str] = None
    roles: Optional[str] = None
    counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def banner(kind: str, name: str) -> str:
    """The banner comment an imported statement is written under."""
    rule = "-- " + "-" * 77
    return f"{rule}\n-- {kind} `{name}`\n{rule}\n"


def idempotent_create(kind: str, name: str, sql: str) -> str:
    """A CREATE statement of section 150, safe to run where the object exists."""
    statement = strip_statement(sql)
    if kind in ("VIEW", "EVENT"):
        statement = re.sub(
            r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?", "CREATE OR REPLACE ", statement,
            count=1, flags=re.IGNORECASE,
        )
        return f"{statement}%%"
    return f"DROP {kind} IF EXISTS {quote_identifier(name)}%%\n{statement}%%"


def delimiter_block(statements: list, title: str) -> Optional[str]:
    """Statements ending with %%, in a DELIMITER block of their own."""
    if not statements:
        return None
    return f"-- {title}\n\nDELIMITER %%\n\n" + "\n\n".join(statements) + "\n\nDELIMITER ;"


# A DEFINER clause as SHOW CREATE writes it: DEFINER=`user`@`host`,
# DEFINER=CURRENT_USER or DEFINER=`role`
_QUOTED = r"(?:`(?:[^`]|``)*`|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\")"
DEFINER = re.compile(
    rf"\s+DEFINER\s*=\s*(?:CURRENT_USER(?:\s*\(\s*\))?|(?:{_QUOTED}|[\w.$-]+)"
    rf"(?:\s*@\s*(?:{_QUOTED}|[\w.%$-]+))?)",
    re.IGNORECASE,
)
# What a qualifier is never looked for in: other quoted names, strings and comments
_QUOTED_OR_COMMENT = rf"{_QUOTED}|--[^\n]*|#[^\n]*|/\*.*?\*/"


def without_definer(sql: str) -> str:
    """A CREATE statement without its DEFINER clause, so it creates the
    object for whoever runs it."""
    return DEFINER.sub("", sql, count=1)


def unqualified(sql: str, schema: str) -> str:
    """The statement without the schema's own name in front of the object
    names it qualifies (`schema`.`t` becomes `t`), so it runs in any schema
    it is used in; strings and comments are left as they are."""
    # The qualifier first: where it starts, it wins over the quoted name. The
    # name it qualifies is taken with it, so that a table named like the
    # schema keeps its place in `schema`.`schema`.`column`.
    pattern = re.compile(
        rf"{re.escape(quote_identifier(schema))}\s*\.\s*(?P<name>`(?:[^`]|``)*`)"
        rf"|(?P<keep>{_QUOTED_OR_COMMENT})",
        re.DOTALL,
    )
    return pattern.sub(
        lambda m: m.group("name") if m.group("name") is not None else m.group("keep"), sql
    )
