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

"""Writing MSM sections of a development script, inline or in breakout files.

A section's body is kept inline in the development script while it is at
most the project's maxInlineSectionSize characters; a larger one goes into
breakout files under development/sections/, one per part of the section
(e.g. 140-10_tables.sql, 140-20_table_additions.sql), each starting with the
project's notices, which the section includes by a SOURCE directive that
leaves them out. A project may set maxInlineSectionSize high enough to keep
its whole script in one file.
"""

import functools
import os
import re
from dataclasses import dataclass
from typing import Optional

from msm_plugin.lib import lsp, management, source
from msm_plugin.lib import statements as statements_lib
from msm_plugin.lib.project_changes import changes_project

# A section body larger than this is kept in breakout files, unless the
# project's msm.project.json sets maxInlineSectionSize.
DEFAULT_MAX_INLINE_SECTION_SIZE = 16 * 1024

BREAKOUT_FOLDER = "sections"

@dataclass(frozen=True)
class PartLayout:
    """A part of a section and its breakout file.

    Attributes:
        file_name: The breakout file's name.
        title: What it holds, for the file's header.
        kinds: The statements.object_kind values it takes; None takes every
            statement of the section.
    """

    file_name: str
    title: str
    kinds: Optional[frozenset]


# The parts of the sections kept by object kind, in the order they run.
# Section 140 runs with FOREIGN_KEY_CHECKS=0 (Section 010), so a table may be
# created before the one its foreign key refers to. In 150 the routines come
# first: CREATE VIEW fails on a function that does not exist yet (1305),
# while a routine, trigger or event body is not checked when it is created.
SECTION_PARTS = {
    "140": (
        PartLayout("140-10_tables.sql", "TABLEs", frozenset({"TABLE"})),
        PartLayout("140-20_table_additions.sql",
                   "Foreign keys and indexes added once all TABLEs exist",
                   frozenset({"TABLE_ADDITION"})),
        PartLayout("140-30_inserts.sql", "INSERTs", frozenset({"INSERT"})),
    ),
    "150": (
        PartLayout("150-10_procedures_functions.sql", "PROCEDUREs and FUNCTIONs",
                   frozenset({"PROCEDURE", "FUNCTION"})),
        PartLayout("150-20_views.sql", "VIEWs", frozenset({"VIEW"})),
        PartLayout("150-30_triggers.sql", "TRIGGERs", frozenset({"TRIGGER"})),
        PartLayout("150-40_events.sql", "EVENTs", frozenset({"EVENT"})),
    ),
    "170": (
        PartLayout("170_roles.sql", "ROLEs and GRANTs", None),
    ),
}


def get_max_inline_section_size(project_settings: dict) -> int:
    """The largest section body kept inline in the development script."""
    value = project_settings.get("maxInlineSectionSize", DEFAULT_MAX_INLINE_SECTION_SIZE)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            "The project setting maxInlineSectionSize has to be a number of "
            f"characters, not `{value}`."
        )
    return value


def breakout_header(project_settings: dict, section_id: str, title: str) -> str:
    """The first lines of a breakout file: the project's notices and what it holds."""
    notices = management.render_copyright_notices(project_settings, prefix="-- ")
    return (
        f"{notices}\n"
        "-- -----------------------------------------------------------------------------\n"
        f"-- MSM Section {section_id}: {title}\n"
    )


def header_end(header: str) -> str:
    """The SOURCE start bound that leaves a breakout file's header out."""
    return f"ln{header.count(chr(10)) + 1}"


def include_directive(file_name: str, header: str) -> str:
    """The SOURCE directive including a breakout file without its header."""
    return f"SOURCE './{BREAKOUT_FOLDER}/{file_name}'[{header_end(header)}:]; -- Without the header"


def shift_include_start(script: str, file_name: str, delta: int) -> str:
    """The script with the start bound of each include_directive of a
    breakout file moved by delta lines, where its header grew by delta."""
    directive = re.compile(
        re.escape(f"SOURCE './{BREAKOUT_FOLDER}/{file_name}'[ln") + r"(\d+):"
    )
    return directive.sub(lambda m: m.group(0).replace(
        f"ln{m.group(1)}:", f"ln{int(m.group(1)) + delta}:"), script)


def set_section_body(section: dict, body: str) -> None:
    """Replaces the SQL of a section (of get_file_sections) with body.

    A blank line after the banner and two before the next one, as the
    templates have them; set_sql_content strips both.
    """
    section["full_content"] = (
        section["full_content"][: section["sql_start"]] + "\n\n" + body + "\n\n"
    )


def read_if_exists(path: str) -> Optional[str]:
    """A file's text, or None where there is no such file."""
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        return f.read()


def write_if_changed(path: str, text: str, old: Optional[str]) -> bool:
    """Writes a file unless it holds the text already; says whether it wrote."""
    if old == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return True


@dataclass
class Part:
    """A part of a section, kept in a breakout file of its own.

    Attributes:
        file_name: The breakout file's name, e.g. 140-10_tables.sql.
        title: What it holds, for the file's header.
        sql: Its SQL; a part without SQL is left out.
    """

    file_name: str
    title: str
    sql: str


def breakout_text(project_settings: dict, section_id: str, part: Part) -> tuple:
    """(header, whole text) of a part's breakout file."""
    header = breakout_header(project_settings, section_id, part.title)
    return header, header + part.sql.strip("\n") + "\n"


def breakout_path(development: str, file_name: str) -> str:
    """A breakout file's path, checked as an include read by msm is."""
    return source.checked_include_path(os.path.join(development, BREAKOUT_FOLDER, file_name))


def section_body(
    project_settings: dict,
    development: str,
    section_id: str,
    parts: list,
    always_breakout: bool = False,
    inline_before: tuple = (),
    inline_after: tuple = (),
) -> tuple:
    """Builds a section's body from its parts, writing their breakout files.

    The parts are joined into the body while that fits maxInlineSectionSize;
    else (or with always_breakout) every part goes into its breakout file and
    the body includes each. Every file written goes through
    source.checked_include_path, as an include read by msm does.

    Args:
        project_settings: The project's msm.project.json.
        development: The development folder.
        section_id: The section, e.g. "140".
        parts: The Parts with SQL, in order.
        always_breakout: Write the breakout files whatever the size.
        inline_before: Texts kept inline before the parts.
        inline_after: Texts kept inline after them.

    Returns:
        (body, the breakout files written).
    """
    written = []
    body = "\n\n".join(part.sql.strip("\n") for part in parts)
    if always_breakout or len(body) > get_max_inline_section_size(project_settings):
        directives = []
        for part in parts:
            header, text = breakout_text(project_settings, section_id, part)
            path = breakout_path(development, part.file_name)
            if write_if_changed(path, text, read_if_exists(path)):
                written.append(path)
            directives.append(include_directive(part.file_name, header))
        body = "\n\n".join(directives)
    texts = (*inline_before, body, *inline_after)
    return "\n\n".join(text.strip("\n") for text in texts if text.strip()), written


@changes_project
def write_sections_parts(schema_project_path: str, parts_by_section: dict) -> list:
    """Sets the bodies of sections of the project's development script (section_body).

    The development script is written once, for every section given.

    Args:
        schema_project_path: The project folder.
        parts_by_section: The Parts of each section, e.g. {"140": [...]}, in
            order; a section without a part that has SQL is left as it is.

    Returns:
        The files written; none when no part has SQL.
    """
    parts_by_section = {
        section_id: [part for part in parts if part.sql and part.sql.strip()]
        for section_id, parts in parts_by_section.items()
    }
    parts_by_section = {section_id: parts for section_id, parts in parts_by_section.items() if parts}
    if not parts_by_section:
        return []
    settings = management.get_project_settings(schema_project_path)
    dev_file = management.get_schema_development_file_path(schema_project_path)
    sections = management.get_file_sections(dev_file)
    written = []
    for section_id, parts in parts_by_section.items():
        if section_id not in sections:
            raise ValueError(f"The development script has no MSM Section {section_id}.")
        body, files = section_body(settings, os.path.dirname(dev_file), section_id, parts)
        set_section_body(sections[section_id], body)
        written += files
    management.write_sections_to_file(dev_file, sections, overwrite_existing=True)
    return [dev_file, *written]


def write_section_parts(schema_project_path: str, section_id: str, parts: list) -> list:
    """Sets the body of one section of the project's development script (write_sections_parts)."""
    return write_sections_parts(schema_project_path, {section_id: parts})


@functools.lru_cache(maxsize=1)
def _template_section_ids() -> tuple:
    """The numbered sections of the development script template."""
    with open(management.DEVELOPMENT_SCRIPT_TEMPLATE, "r") as f:
        return tuple(
            section_id for section_id in management.get_script_sections(f.read())
            if section_id.isdigit()
        )


def _development_version(schema_project_path: str, sections: dict) -> str:
    """The script's version (Section 910), else the one after the last release."""
    if "910" in sections:
        return management.get_schema_development_version(sections=sections)
    last = management.get_last_released_version(schema_project_path)
    return f"{last[0]}.{last[1]}.{last[2] + 1}" if last else "0.0.1"


def complete_sections(
    schema_project_path: str, sections: dict, project_settings: dict = None
) -> tuple:
    """Adds every section of the template the development script lacks.

    Each goes before the first section with a higher number, as the
    template has it, so the script is complete again: projects made before
    a section existed (160), or edited by hand, lack some. The added
    sections, and the one that was last before, end with the line breaks the
    template has; the others stay as written.

    Args:
        schema_project_path: The project folder.
        sections: The script's sections, from get_script_sections.
        project_settings: Its msm.project.json, if read already.

    Returns:
        (sections, the ids added). The sections are those given when none
        is missing.
    """
    missing = [section_id for section_id in _template_section_ids() if section_id not in sections]
    if not missing:
        return sections, []
    if project_settings is None:
        project_settings = management.get_project_settings(schema_project_path)
    template = management.get_script_sections(management.render_template_file(
        management.DEVELOPMENT_SCRIPT_TEMPLATE,
        management.development_script_substitutions(
            project_settings, _development_version(schema_project_path, sections)
        ),
    ))
    pairs = list(sections.items())
    for section_id in missing:
        position = next(
            (index for index, (other, _) in enumerate(pairs)
             if other.isdigit() and int(other) > int(section_id)),
            len(pairs),
        )
        pairs.insert(position, (section_id, template[section_id]))
    last_before = next(reversed(sections), None)
    for index, (section_id, section) in enumerate(pairs):
        is_last = index + 1 == len(pairs)
        if section_id in missing or (section_id == last_before and not is_last):
            text = section["full_content"].rstrip("\n") + ("\n" if is_last else "\n\n")
            pairs[index] = (section_id, {**section, "full_content": text})
    return dict(pairs), missing


# The commented-out examples of the templates (templates/scripts), in
# Sections 140 and 150 of a new development script: a block comment whose
# first line is the template's `-- ToDo: Add schema ... create statements`
# note (a ToDo block comment of the user's own stays). Every existing
# project has this text, so the pattern stays as it is even where the
# templates change.
_TEMPLATE_EXAMPLE = re.compile(
    r"[ \t]*/\*[ \t]*\n[ \t]*--[ \t]*ToDo: Add schema (?:TABLEs|objects) create statements"
    r"\n.*?\*/[ \t]*\n?",
    re.DOTALL,
)
_TEMPLATE_EXAMPLE_MARK = "-- ToDo:"
# What Section 150's example leaves behind: its DELIMITER pair around nothing.
_EMPTY_DELIMITER_PAIR = re.compile(r"^DELIMITER %%\n\s*DELIMITER ;\n?", re.MULTILINE)


def without_template_examples(script: str, section_ids=None) -> str:
    """The script without the templates' commented-out examples in the
    sections that have a statement of their own (or a SOURCE line): they
    make sense only while a section is empty. Other sections, and every
    other comment, are left as they are.

    Args:
        script: The development script.
        section_ids: The sections to look at; all by default.

    Returns:
        The script; the one given where no example goes.
    """
    if _TEMPLATE_EXAMPLE_MARK not in script:
        return script
    sections = management.get_script_sections(script)
    changed = False
    for section_id, section in sections.items():
        if section_ids is None or section_id in section_ids:
            changed = drop_template_example(section) or changed
    if not changed:
        return script
    return "\n".join(section["full_content"] for section in sections.values())


def drop_template_example(section: dict) -> bool:
    """Drops the template's example from a section (of get_script_sections)
    that has a statement of its own, see without_template_examples.

    Returns:
        Whether it had one to drop.
    """
    body, found = _TEMPLATE_EXAMPLE.subn("", section["full_content"][section["sql_start"]:])
    if not found or not statements_lib.strip_statement(body):
        return False
    set_section_body(section, _EMPTY_DELIMITER_PAIR.sub("", body).strip("\n"))
    return True


def complete_script(
    schema_project_path: str, script: str, project_settings: dict = None
) -> tuple:
    """A development script with every template section it lacks (complete_sections).

    Returns:
        (script, its sections, the ids added). The script is the one given
        when none is missing.
    """
    sections, added = complete_sections(
        schema_project_path, management.get_script_sections(script), project_settings
    )
    if added:
        script = "\n".join(section["full_content"] for section in sections.values())
        sections = management.get_script_sections(script)
    return script, sections, added


@dataclass
class SplitResult:
    """What split_sections did.

    Attributes:
        split: The sections split, each with the breakout files it includes.
        skipped: The sections left as they are, each with the reason.
        sections_added: The template sections the script lacked.
        files: The files written.
    """

    split: dict
    skipped: dict
    sections_added: list
    files: list


def _places(section_id: str, cut) -> list:
    """The PartLayout each statement of a cut section goes to, None for none."""
    layouts = SECTION_PARTS[section_id]
    return [
        next((layout for layout in layouts
              if layout.kinds is None or statement.kind in layout.kinds), None)
        for statement in cut.statements
    ]


def split_sections(
    schema_project_path: str, section_ids: list = None, lsp_path: str = None
) -> SplitResult:
    """Splits sections of the development script into breakout files by object kind.

    Section 140 into 140-10_tables.sql, 140-20_table_additions.sql and
    140-30_inserts.sql, 150 into 150-10_procedures_functions.sql,
    150-20_views.sql, 150-30_triggers.sql and 150-40_events.sql, 170 into
    170_roles.sql (SECTION_PARTS), whatever the section's size; the section
    then includes each by a SOURCE directive. A statement keeps the comments
    above it, and the statements of a part keep their order. The section's
    text above its first statement's comments, and after its last statement,
    stays inline, as do statements of no part's kind before the first or
    after the last statement that moves. The statements are found by the
    mariadb-lsp binary's splitter.

    A section is left as it is, and reported, when it includes a file
    already, has no statement to move, has a statement of no part's kind
    between two that move (moving them could change what it does), or when
    a breakout file it would write exists with other content. Every template
    section the script lacks is added first. Nothing is written before every
    section is planned; the development script is written once.

    Args:
        schema_project_path: The project folder.
        section_ids: The sections to split; by default 140, 150 and 170.
        lsp_path: The mariadb-lsp binary or its folder.

    Returns:
        The SplitResult.
    """
    section_ids = [str(id) for id in section_ids] if section_ids else list(SECTION_PARTS)
    unknown = [section_id for section_id in section_ids if section_id not in SECTION_PARTS]
    if unknown:
        raise ValueError(
            f"MSM Section {', '.join(unknown)} cannot be split; the sections split by object "
            f"kind are {', '.join(SECTION_PARTS)}."
        )
    binary = lsp.require_lsp(lsp_path)
    settings = management.get_project_settings(schema_project_path)
    dev_file = management.get_schema_development_file_path(schema_project_path)
    development = os.path.dirname(dev_file)

    with open(dev_file, "r") as f:
        script = f.read()
    script, sections, added = complete_script(schema_project_path, script, settings)
    entries = statements_lib.sql_entries(script, lsp.split(binary, script))

    result = SplitResult(split={}, skipped={}, sections_added=added, files=[])
    plans = {}
    for section_id in section_ids:
        section = sections[section_id]
        if source.contains_source_directive(section["sql_content"]):
            result.skipped[section_id] = "It includes a file already."
            continue
        try:
            cut = statements_lib.cut_section(
                script, section["start_position"] + section["sql_start"],
                section["end_position"], entries,
            )
        except ValueError as e:
            result.skipped[section_id] = str(e)
            continue
        places = _places(section_id, cut)
        moved = [index for index, layout in enumerate(places) if layout is not None]
        if not moved:
            result.skipped[section_id] = "It has no statement to move."
            continue
        first, last = moved[0], moved[-1] + 1
        stuck = next((statement for statement, layout
                      in zip(cut.statements[first:last], places[first:last]) if layout is None),
                     None)
        if stuck is not None:
            result.skipped[section_id] = (
                "It has a statement no breakout file takes between statements that would "
                "move: " + statements_lib.first_line(stuck.text)
            )
            continue
        parts = [
            Part(layout.file_name, layout.title, statements_lib.render(
                [s for s, place in zip(cut.statements, places) if place is layout]))
            for layout in SECTION_PARTS[section_id]
        ]
        parts = [part for part in parts if part.sql]
        conflicts = [
            part.file_name for part in parts
            if read_if_exists(breakout_path(development, part.file_name))
            not in (None, breakout_text(settings, section_id, part)[1])
        ]
        if conflicts:
            result.skipped[section_id] = (
                f"The breakout file(s) {', '.join(conflicts)} exist already with other content."
            )
            continue
        plans[section_id] = (
            parts,
            (cut.preamble, statements_lib.render(cut.statements[:first])),
            (statements_lib.render(cut.statements[last:]), cut.trailer),
        )

    files = {}
    for section_id, (parts, before, after) in plans.items():
        body, written = section_body(
            settings, development, section_id, parts, always_breakout=True,
            inline_before=before, inline_after=after,
        )
        set_section_body(sections[section_id], body)
        # The example would stay behind the SOURCE lines.
        drop_template_example(sections[section_id])
        result.split[section_id] = [part.file_name for part in parts]
        files.update(dict.fromkeys(written))
    if plans or added:
        management.write_sections_to_file(dev_file, sections, overwrite_existing=True)
        result.files = [dev_file, *files]
    return result

