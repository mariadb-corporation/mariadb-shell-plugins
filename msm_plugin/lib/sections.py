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

import os
from dataclasses import dataclass

from msm_plugin.lib import management, source

# A section body larger than this is kept in breakout files, unless the
# project's msm.project.json sets maxInlineSectionSize.
DEFAULT_MAX_INLINE_SECTION_SIZE = 16 * 1024

BREAKOUT_FOLDER = "sections"


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


def include_directive(file_name: str, header: str) -> str:
    """The SOURCE directive including a breakout file without its header."""
    return (
        f"SOURCE './{BREAKOUT_FOLDER}/{file_name}'[ln{header.count(chr(10)) + 1}:]; "
        "-- Without the header"
    )


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


def write_section_parts(schema_project_path: str, section_id: str, parts: list) -> list:
    """Sets the body of a section of the project's development script.

    The parts are joined into the section's body while that fits
    maxInlineSectionSize; else every part is written into its breakout file
    and the section includes each. The section's former body is replaced.
    Every file written goes through source.checked_include_path, as an
    include read by msm does.

    Args:
        schema_project_path: The project folder.
        section_id: The section, e.g. "140".
        parts: The Parts, in order.

    Returns:
        The files written; none when no part has SQL.
    """
    parts = [part for part in parts if part.sql and part.sql.strip()]
    if not parts:
        return []
    settings = management.get_project_settings(schema_project_path)
    dev_file = management.get_schema_development_file_path(schema_project_path)
    development = os.path.dirname(dev_file)
    written = []

    body = "\n\n".join(part.sql.strip("\n") for part in parts)
    if len(body) > get_max_inline_section_size(settings):
        directives = []
        for part in parts:
            header = breakout_header(settings, section_id, part.title)
            path = source.checked_include_path(
                os.path.join(development, BREAKOUT_FOLDER, part.file_name)
            )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(header + part.sql.strip("\n") + "\n")
            written.append(path)
            directives.append(include_directive(part.file_name, header))
        body = "\n\n".join(directives)

    sections = management.get_file_sections(dev_file)
    if section_id not in sections:
        raise ValueError(f"The development script has no MSM Section {section_id}.")
    section = sections[section_id]
    # A blank line after the banner and two before the next one, as the
    # templates have them.
    section["full_content"] = (
        section["full_content"][: section["sql_start"]] + "\n\n" + body + "\n\n"
    )
    management.write_sections_to_file(dev_file, sections, overwrite_existing=True)
    written.insert(0, dev_file)
    return written
