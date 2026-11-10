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

# Define plugin version

from msm_plugin import lib
import json
import os
from pathlib import Path
import re
import shutil
from string import Template
from datetime import date
from textwrap import indent
import mysqlsh

from msm_plugin.lib import online_alter as online_alter_lib
from msm_plugin.lib import update_steps
from msm_plugin.lib.source import expand_source_directives

# Regex to match MSM sections, excluding the license and the last section
MSM_SECTIONS_REGEX = r"--\s+#+\s+--\s+MSM\s+Section\s+(\d+):\s*(.*?)$\s+.*?--\s+#+"

# Regex to match the MSM LOOP UPDATABLE-VERSIONS
MSM_LOOP_UPDATABLE_VERSIONS_REGEX = (
    r"--\s+###\s+MSM-LOOP-START:UPDATABLE-VERSIONS(\(indent=(\d+)\))?.*?\n(.*?)"
    r"--\s+###\s+MSM-LOOP-END:UPDATABLE-VERSIONS.*?\n"
)

# Reges to match the MSM section placeholder
MSM_SECTION_PLACEHOLDER_REGEX = r"\$\{section_(\d+).*?\}\n"

# A substitution placeholder in the SQL scripts of a project, e.g.
# /*<msm:schema_prefix>*/. Run as is, the comment is whitespace; msm.deploySchema()
# replaces it with the value given for the name (see resolve_substitutions()).
MSM_SUBSTITUTION_REGEX = r"/\*<msm:([A-Za-z_][A-Za-z0-9_]*)>\*/"

# Every substitution value has to match this, whatever the project declares:
# the values are written into the scripts as parts of unquoted identifiers.
MSM_SUBSTITUTION_VALUE_REGEX = r"[A-Za-z0-9_]*"

# Regex to match the leading comments and empty lines before SQL commands,
# but ensure that comments before SQL commands are kept
REMOVE_LEADING_COMMENTS_AND_EMPTY_LINES = r"((^--.*?\n)+(^\s*\n)*)"

# Regex to match all empty lines, including whitespace, at the end of the file
REMOVE_TRAILING_EMPTY_LINES = r"(\s*\Z)"

# --- Copyright notices -----------------------------------------------------
#
# A project records its copyright notices in the `copyrights` list of its
# msm.project.json file, one entry per holder:
#
#   { "holder": "MariaDB plc.",
#     "yearOfCreation": "2026",
#     "yearOfLastUpdate": "2027",
#     "tracksUpdates": true }
#
# The holder is written into a notice as it is stored, so a final period is
# part of the holder: "Copyright (c) 2026, MariaDB plc." holds "MariaDB plc.".
#
# The years are stored, never worked out from today's date when a file is
# written. `tracksUpdates` marks the holders that maintain the project: when a
# change is applied to the project in a later year (update_copyright_years),
# their `yearOfLastUpdate` is set to that year in msm.project.json and the
# notices of the development files are brought along, so a notice reads
# "2026", then "2026, 2027", then "2026, 2028". Notices inherited from an
# upstream project are left frozen at the years they carry, as a change made
# downstream does not extend the copyright of the upstream holder.
#
# Projects created before this list existed hold a single `copyrightHolder`
# string and a single `yearOfCreation` instead. Those are still read and turned
# into a one entry list, so existing project folders keep working untouched.

# Regex to match a copyright notice, capturing the year of creation, the
# optional year of the last update and the holder. Used to take notices apart
# again, be it in a template file or in a `copyrightHolder` value that had a
# second notice squeezed into it.
COPYRIGHT_NOTICE_REGEX = re.compile(
    r"Copyright\s+(?:\(c\)|&copy;|©)\s+(\d{4})(?:\s*,\s*(\d{4}))?\s*,?\s*(.*?)\s*$",
    re.IGNORECASE,
)

# Regex to match a run of consecutive copyright notice lines, so a whole notice
# block can be replaced in one go instead of line by line.
COPYRIGHT_BLOCK_REGEX = re.compile(r"Copyright.*(?:\nCopyright.*)*$", re.MULTILINE)

# Regex to match the comment tokens a notice line may carry when it was taken
# from a source file, either in front of it ("-- ", " * ", "# ", "<!-- ") or
# behind it ("*/", "-->").
COPYRIGHT_COMMENT_PREFIX_REGEX = re.compile(r"^[\s*#/<!-]*")
COPYRIGHT_COMMENT_SUFFIX_REGEX = re.compile(r"(\*/|-->)\s*$")

MSM_SCHEMA_VERSION_VIEW_VALUES = (
    r"CREATE.*?VIEW.*?`msm_schema_version`.*?"
    r"\(.*?`major`.*?`minor`.*?`patch`.*?\).*?AS.*?SELECT.*?"
    r"((\d+).*?,.*?(\d+).*?,.*?(\d+))"
)

MSM_SECTION_REMOVE_COMMENTS_AND_DELIMITERS = (
    r"(^\s*--\s+.*?$)|(^DELIMITER\s+.*?$)|(\/\*.*?\*\/)"
)


def remove_leading_comments_trailing_lines(section: str) -> str:
    """Removes leading comments and empty lines at the beginning and end from the given section

    Args:
        section (str): The section to remove the leading comments from

    Returns:
        The trimmed section text
    """
    lines = section.splitlines()

    # Remove leading comments and empty lines
    while len(lines) > 0 and (lines[0].startswith("-- ") or lines[0].strip() == ""):
        lines = lines[1:]

    # Remove trailing empty lines
    while len(lines) > 0 and lines[len(lines) - 1].strip() == "":
        lines = lines[: len(lines) - 1]

    return "\n".join(lines)


def get_sql_content(full_section_content: str) -> tuple[str, int, int]:
    """Returns the sql content of a MSM section

    Args:
        full_section_content: The full content of the section

    Returns:
        The sql content as well as the start and the end position
    """
    # Search start position of sql content
    start_position = 0
    match = re.search(
        REMOVE_LEADING_COMMENTS_AND_EMPTY_LINES,
        full_section_content,
        re.MULTILINE | re.DOTALL,
    )
    if match is not None:
        start_position = match.end()

    # Search end position of sql content
    end_position = len(full_section_content)
    match = re.search(
        REMOVE_TRAILING_EMPTY_LINES, full_section_content, re.MULTILINE | re.DOTALL
    )
    if match is not None:
        end_position = match.start()

    return (
        full_section_content[start_position:end_position],
        start_position,
        end_position,
    )


def set_sql_content(section: dict, sql_content: str) -> None:
    """Sets the sql content of a MSM section

    Args:
        section: The MSM section as dict

    Returns:
        None
    """
    if not (
        "sql_content" in section and "sql_start" in section and "sql_end" in section
    ):
        raise ValueError("The given section does not contain SQL content.")

    section["full_content"] = (
        section["full_content"][: section["sql_start"]] + "\n" + sql_content.strip("\n")
    )
    section["sql_content"] = sql_content
    section["sql_end"] = len(section["full_content"])


def get_script_sections(script: str) -> dict[str, dict]:
    """Returns the MSM sections of a script

    Args:
        script (str): The script as string

    Returns:
        A list of script dicts.
    """

    # Search for MSM sections
    matches = re.finditer(MSM_SECTIONS_REGEX, script, re.MULTILINE | re.DOTALL)

    # for match_id, match in enumerate(matches, start=1):
    #     print("Match {match_id} was found at {start}-{end}: {match}".format(
    #         match_id=match_id, start=match.start(), end=match.end(), match=match.group()))
    #     for group_id in range(0, len(match.groups())):
    #         group_id = group_id + 1
    #         print("Group {group_id} found at {start}-{end}: {group}".format(group_id=group_id,
    #               start=match.start(group_id), end=match.end(group_id), group=match.group(group_id)))

    section_headers = []
    for match_id, match in enumerate(matches, start=1):
        # Add license section if available
        if match_id == 1 and match.start() > 0:
            section_headers.append(
                {
                    "section_id": "license",
                    "start_position": 0,
                    "header_length": 0,
                }
            )

        section_headers.append(
            {
                "section_id": match.group(1),
                "start_position": match.start(),
                "header_length": match.end() - match.start(),
            }
        )

    sections = {}

    for idx, header in enumerate(section_headers):
        # Lookup the end of the section by checking the start of the next section,
        # while the last section ends at the end of the script.
        if idx + 1 < len(section_headers):
            section_end = section_headers[idx + 1]["start_position"] - 1
        else:
            section_end = len(script)

        sections[header["section_id"]] = {
            "full_content": script[header["start_position"] : section_end],
            "sql_content": script[
                header["start_position"] + header["header_length"] : section_end
            ],
            "sql_start": header["header_length"],
            "sql_end": section_end,
            "start_position": header["start_position"],
            "end_position": section_end,
        }

    return sections


def get_file_sections(file_path: str) -> str:
    """Returns the MSM sections of a given file

    Args:
        file_path (str): The path of the SQL file.
        section_id (str): The id of the section.

    Returns:
        The SQL content as string
    """
    if not os.path.exists(file_path):
        raise ValueError(f"The file `{file_path}` could not be found.")

    with open(file_path, "r") as f:
        script = f.read()

    return get_script_sections(script)


def get_file_section(file_path: str, section_id: str) -> str:
    """Returns the SQL content of a MSM section of a given file

    Args:
        file_path (str): The path of the SQL file.
        section_id (str): The id of the section.

    Returns:
        The SQL content as string
    """
    sections = get_file_sections(file_path)

    if section_id in sections:
        return sections[section_id]
    else:
        raise ValueError(
            f"The MSM section `{section_id}` could not be found in the file `{file_path}`."
        )


def set_section_sql_content(
    section_id: str,
    sql_content: str,
    file_path: str = None,
    sections: dict[str, dict] = None,
) -> None:
    """Sets the SQL content of a MSM section of a file

    Args:
        section_id: The id of the section.
        sql_content: The sql content to set
        file_path: The path of the SQL file.
        sections: The MSM sections.

    Returns:
        None
    """
    if sections is None and file_path is not None:
        # A change applied to a project's file moves its copyright years along
        schema_project_path = find_schema_project_path(file_path)
        if schema_project_path is not None:
            update_copyright_years(schema_project_path)
        sections = get_file_sections(file_path)
    elif sections is None:
        raise ValueError("Either a file_path or sections must be set.")

    if not section_id in sections:
        raise ValueError(f"The given section `{section_id}` was not found.")

    section = sections[section_id]
    set_sql_content(section=section, sql_content=sql_content)

    if file_path is not None:
        write_sections_to_file(
            file_path=file_path, sections=sections, overwrite_existing=True
        )


def write_sections_to_file(
    file_path: str, sections: dict[str, dict], overwrite_existing: bool = False
):
    """Writes all MSM sections to a file

    Args:
        file_path: The path write the sections to.
        sections: The MSM sections to write out.
        overwrite_existing: Whether an existing file should be replaced. Defaults to False.

    Returns:
        None
    """
    if os.path.exists(file_path) and not overwrite_existing:
        raise ValueError(
            f"The file {file_path} already exists. Please explicitly allow to replace existing files."
        )

    with open(file_path, "w") as f:
        f.write(
            "\n".join(
                sections[section_id].get("full_content") for section_id in sections
            )
        )


def convert_string_to_valid_filename(name: str) -> str:
    """Converts a string to a valid filename

    Replaces all spaces with underscores and remove characters that are not allowed.

    Args:
        name: The string to convert

    Returns:
        The converted filename
    """
    # Replace all spaces with underscores
    r = re.compile(r"\s+", re.MULTILINE)
    file_name = r.sub("_", name)

    # Remove characters that are not allowed
    r = re.compile(r"[\\\\/:*?\"<>|]", re.MULTILINE)
    file_name = r.sub("", file_name)

    return file_name


def check_mysql_identifier(identifier: str, must_be_usable_when_unquoted: bool = True):
    """Checks if the given identifier is valid

    Args:
        identifier: The identifier to be checked.
        must_be_usable_when_unquoted: Whether it has to be usable without
            quotes: basic Latin letters, digits, dollar and underscore, not
            starting with a dollar and not digits only. False where special
            characters are allowed, which takes any identifier.

    Raises:
        ValueError: Where it is missing or empty, or has to be usable without
            quotes and is not.
    """
    if not identifier:
        raise ValueError("A schema name has to be given.")
    if must_be_usable_when_unquoted:
        if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_$]*$", identifier):
            raise ValueError(
                "Only basic Latin letters, digits 0-9, dollar, underscore are allowed to be used as schema name. "
                "Use the allow_special_chars option to overwrite this behavior."
            )
        if re.match(r"^[0-9]+$", identifier):
            raise ValueError(
                "A schema name must not consist solely of digits. "
                "Use the allow_special_chars option to overwrite this behavior."
            )


def get_project_copyrights(project_settings: dict) -> list:
    """Returns the copyright notices of a project.

    Projects that predate the `copyrights` list only hold a single
    `copyrightHolder` string. In some of them a second notice was squeezed into
    that string, complete with the comment prefix of the file it was written
    for, so the value is taken apart again here.

    Args:
        project_settings: The settings of the project.

    Returns:
        The list of copyright entries. When the list had to be derived, the last
        entry is the one tracking updates, as the holder added most recently is
        taken to be the one maintaining the project.
    """
    copyrights = project_settings.get("copyrights")
    if copyrights:
        return copyrights

    year_of_creation = project_settings.get("yearOfCreation") or date.today().strftime(
        "%Y"
    )

    copyrights = []
    for line in str(project_settings.get("copyrightHolder") or "").splitlines():
        line = COPYRIGHT_COMMENT_PREFIX_REGEX.sub("", line)
        line = COPYRIGHT_COMMENT_SUFFIX_REGEX.sub("", line).strip()
        if line == "":
            continue

        notice = COPYRIGHT_NOTICE_REGEX.search(line)
        if notice is None:
            # A plain holder name, the common case, taking its year from the
            # project settings.
            copyrights.append({"holder": line, "yearOfCreation": year_of_creation})
            continue

        creation, update, holder = notice.groups()
        copyright = {"holder": holder.strip(), "yearOfCreation": creation}
        if update:
            copyright["yearOfLastUpdate"] = update
        copyrights.append(copyright)

    if not copyrights:
        copyrights = [{"holder": "", "yearOfCreation": year_of_creation}]

    for pos, copyright in enumerate(copyrights):
        copyright["tracksUpdates"] = pos == len(copyrights) - 1

    return copyrights


def current_year() -> str:
    """The current year, as the copyright entries store their years."""
    return date.today().strftime("%Y")


def get_copyright_years(copyright: dict, year: str = None) -> str:
    """Returns the year part of a copyright notice, from the years stored.

    The last-update year is only shown when it differs from the year of
    creation. Neither depends on the date the notice is written on: the
    last-update year is set by update_copyright_years.

    Args:
        copyright: A single copyright entry.
        year: The year of creation of an entry that has none, defaulting to
            the current one.

    Returns:
        Either "<creation>" or "<creation>, <last update>".
    """
    creation = str(copyright.get("yearOfCreation") or year or current_year())
    update = copyright.get("yearOfLastUpdate")

    if not update or str(update) == creation:
        return creation
    return f"{creation}, {update}"


def render_copyright_notices(project_settings: dict, prefix: str = "") -> str:
    """Renders the copyright notices of a project.

    Args:
        project_settings: The settings of the project.
        prefix: The comment prefix to put in front of every line, e.g. " * ".

    Returns:
        One notice per line, without a trailing line break.
    """
    lines = []
    for copyright in get_project_copyrights(project_settings):
        # As stored: a final period is the holder's own.
        holder = str(copyright.get("holder") or "").strip()
        years = get_copyright_years(copyright)
        lines.append(f"{prefix}Copyright (c) {years}, {holder}")

    return "\n".join(lines)


def check_copyright_year(year, name: str = "yearOfCreation") -> str:
    """A year of a copyright entry, as stored: four digits, not in the future.

    Args:
        year: The year, as a string or a number.
        name: Names the value in an error.

    Returns:
        The year as a string.

    Raises:
        ValueError: For anything else.
    """
    text = str(year).strip() if isinstance(year, (str, int)) and not isinstance(year, bool) else ""
    if not re.fullmatch(r"\d{4}", text):
        raise ValueError(f"The {name} has to be a year of four digits, not `{year}`.")
    if text > current_year():
        raise ValueError(f"The {name} {text} lies in the future.")
    return text


def bump_copyright_years(copyrights: list, year: str = None) -> bool:
    """Sets the last-update year of the holders that track updates.

    Args:
        copyrights: The copyright entries, changed in place.
        year: The year a change is applied in, defaulting to the current one.

    Returns:
        Whether an entry changed.
    """
    year = year or current_year()
    changed = False
    for copyright in copyrights:
        if not copyright.get("tracksUpdates"):
            continue
        last = str(copyright.get("yearOfLastUpdate") or copyright.get("yearOfCreation") or "")
        if last < year:
            copyright["yearOfLastUpdate"] = year
            changed = True
    return changed


def _holder_key(holder: str) -> str:
    """A holder as compared with a notice: files written before the holder's
    own final period was kept have one added."""
    return holder.strip().rstrip(".").strip()


def refresh_notice_years(text: str, copyrights: list) -> str:
    """Brings the years of the notices in a file's text up to date.

    Only the first run of notice lines is looked at, the file's header, and
    in it only the years of the holders that track updates are replaced. The
    rest of every line stays as it is, so the line count and anything a user
    wrote around the notices do not change.

    Args:
        text: The text of the file.
        copyrights: The project's copyright entries.

    Returns:
        The text, changed where a notice's years were out of date.
    """
    tracking = {
        _holder_key(str(copyright.get("holder") or "")): copyright
        for copyright in copyrights
        if copyright.get("tracksUpdates")
    }
    if not tracking:
        return text

    lines = text.splitlines(keepends=True)
    pos = next(
        (i for i, line in enumerate(lines) if COPYRIGHT_NOTICE_REGEX.search(line)), None
    )
    if pos is None:
        return text
    while pos < len(lines):
        notice = COPYRIGHT_NOTICE_REGEX.search(lines[pos])
        if notice is None:
            break
        holder = COPYRIGHT_COMMENT_SUFFIX_REGEX.sub("", notice.group(3))
        copyright = tracking.get(_holder_key(holder))
        if copyright is not None:
            end = notice.end(2) if notice.group(2) else notice.end(1)
            line = lines[pos]
            lines[pos] = (
                line[: notice.start(1)] + get_copyright_years(copyright) + line[end:]
            )
        pos += 1
    return "".join(lines)


def _project_files_with_notices(schema_project_path: str, project_settings: dict) -> list:
    """The files of a project whose notices follow the years of the settings.

    The development scripts (the development script, the pending update
    script and the breakout files) and the project's markdown files. Released
    files keep the notices they were released with.
    """
    development = os.path.join(schema_project_path, "development")
    file_name = project_settings.get("schemaFileName")
    files = [
        os.path.join(development, f"{file_name}_next.sql"),
        os.path.join(development, f"{file_name}_update_next.sql"),
    ]
    sections_folder = os.path.join(development, "sections")
    if os.path.isdir(sections_folder):
        files += sorted(
            str(path) for path in Path(sections_folder).glob("*.sql") if path.is_file()
        )
    files += sorted(str(path) for path in Path(schema_project_path).rglob("*.md"))
    return [path for path in files if os.path.isfile(path)]


def find_schema_project_path(file_path: str) -> str | None:
    """The project folder a file belongs to: the closest folder above it that
    holds an msm.project.json file, or None."""
    folder = os.path.dirname(os.path.abspath(os.path.expanduser(file_path)))
    while True:
        if os.path.isfile(os.path.join(folder, "msm.project.json")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def update_copyright_years(schema_project_path: str, year: str = None) -> list:
    """Moves the copyright years along when a change is applied to a project.

    In the first change of a year, the holders that track updates get that
    year as their yearOfLastUpdate in msm.project.json, and the notices of
    the development scripts and the markdown files are brought along. Later
    changes of the same year find nothing to do.

    The development script's header is part of the text the schema
    snapshot's source_hash is taken of. A snapshot that matched the script
    before gets the new hash, as only a comment changed; a client then reads
    the diagrams again before its next msm.editSchema.

    Args:
        schema_project_path: The project folder.
        year: The year the change is applied in, defaulting to the current one.

    Returns:
        The files written; none where the years were up to date.
    """
    settings_path = os.path.join(schema_project_path, "msm.project.json")
    project_settings = get_project_settings(schema_project_path)
    copyrights = [dict(copyright) for copyright in project_settings["copyrights"]]
    if not bump_copyright_years(copyrights, year):
        return []

    # Here and not at the top: lib.modeling imports this module.
    from msm_plugin.lib import modeling

    try:
        project = modeling._Project.load(schema_project_path)
        snapshot = modeling._schema_row(modeling._rows(project))
        carry_hash = modeling._snapshot_is_current(project, snapshot)
    except Exception:
        # A script that cannot be read has no snapshot to keep current.
        carry_hash = False

    # Not get_project_settings' copy, which adds what it derives on reading.
    # A project in the single holder format gets the list written.
    with open(settings_path) as f:
        raw = json.load(f)
    raw["copyrights"] = copyrights
    with open(settings_path, "w") as f:
        f.write(json.dumps(raw, indent=4))
    written = [settings_path]

    for path in _project_files_with_notices(schema_project_path, project_settings):
        with open(path) as f:
            text = f.read()
        refreshed = refresh_notice_years(text, copyrights)
        if refreshed != text:
            with open(path, "w") as f:
                f.write(refreshed)
            written.append(path)

    if carry_hash:
        project = modeling._Project.load(schema_project_path)
        rows = modeling._rows(project)
        snapshot = modeling._schema_row(rows)
        new_hash = modeling._current_hash(project)
        if snapshot["document"].get("source_hash") != new_hash:
            snapshot["document"]["source_hash"] = new_hash
            written += [
                path for path in modeling._write(project, rows) if path not in written
            ]

    return written


def strip_template_copyright_header(lines: list) -> str:
    """Removes the copyright header from the lines of a template file.

    The template files carry this repository's own notices. A file generated
    from a template must not inherit them, it gets the notices of the project's
    own copyright holders instead.

    Only the leading run of notice lines is dropped, so a notice further down
    the file is kept: the license templates hold the repository notices on their
    first lines and the project's own notices a few lines below, and the latter
    have to survive.

    Args:
        lines: The lines of the template file, as returned by readlines()

    Returns:
        The template text without its copyright header.
    """
    pos = 0
    while pos < len(lines) and COPYRIGHT_NOTICE_REGEX.search(lines[pos]):
        pos += 1
    return "".join(lines[pos:])


def get_license_text(
    schema_project_path: str = None, project_settings: dict = None
) -> str:
    """Gets the correct license text for the given project

    The license text is fetch from the right license template and the variables are
    substituted accordingly.

    Args:
        schema_project_path: The MSM project path
        project_settings: Optional project settings

    Returns:
        The converted filename
    """
    if project_settings is None and schema_project_path is not None:
        project_settings = get_project_settings(schema_project_path)
    elif project_settings is None and schema_project_path is None:
        raise ValueError("No schema_project_path nor project_settings parameter given.")

    try:
        license = project_settings.get("license")
        custom_license = project_settings.get("customLicense")
        copyrights = get_project_copyrights(project_settings)
    except Exception as e:
        raise ValueError(
            f"The project settings must include copyrights or copyrightHolder, license and customLicense. {e}"
        )

    license_template = None
    if license.upper() == "CUSTOM":
        if custom_license is not None and custom_license != "":
            license_template = Template(custom_license)
        else:
            raise ValueError("No custom license text specified.")
    else:
        template_folder = os.path.join(Path(__file__).parent.parent, "templates")
        license_file_path = os.path.join(template_folder, "license", f"{license}.txt")
        if not os.path.exists(license_file_path):
            raise ValueError(
                f"No license stored under the given license name `{license}`. Please use a custom license text."
            )
        with open(license_file_path) as f:
            license_template = Template(strip_template_copyright_header(f.readlines()))

    try:
        license_text = license_template.substitute(
            {
                # The notices of all copyright holders, as a block sitting
                # inside the /* */ comment the license templates open.
                "copyright_notices": render_copyright_notices(
                    project_settings, prefix=" * "
                ),
                # The first holder on its own, so a custom license text written
                # against the single holder form keeps working.
                "copyright_holder": str(copyrights[0].get("holder") or "").strip(),
                "year": get_copyright_years(copyrights[0]),
            }
        )
    except:
        raise ValueError(
            "The license template uses a placeholder other than ${copyright_notices}, "
            "${year} or ${copyright_holder}."
        )

    return license_text


def copy_template_file_and_substitute(
    source_file_path: str, target_file_path: str, substitutions: dict
):
    """Copies a template file and substitutes the given variables

    Args:
        source_file_path: The path to the source file
        target_file_path: The path to the target file
        substitutions: The dict holding the substitutions

    Returns:
        None
    """
    script = render_template_file(source_file_path, substitutions)

    with open(target_file_path, "w") as f:
        f.write(script)


def render_template_file(source_file_path: str, substitutions: dict) -> str:
    """A template file's text without its copyright header, placeholders replaced."""
    with open(source_file_path, "r") as f:
        return Template(strip_template_copyright_header(f.readlines())).substitute(substitutions)


DEVELOPMENT_SCRIPT_TEMPLATE = os.path.join(
    Path(__file__).parent.parent, "templates", "scripts", "schema_next.sql"
)


def development_script_substitutions(project_settings: dict, version: str) -> dict:
    """The values the development script template is filled with."""
    schema_name = project_settings.get("schemaName") or ""
    return {
        "license": get_license_text(project_settings=project_settings),
        "schema_name": schema_name,
        "schema_identifier": get_schema_identifier(schema_name),
        "version_str": version,
        "version_comma_str": ", ".join(
            str(number) for number in lib.core.convert_version_str_to_list(version)
        ),
    }


def project_folder_path(target_path: str, schema_name: str) -> str:
    """The folder create_schema_project_folder makes for a schema in target_path."""
    return os.path.join(
        os.path.abspath(os.path.expanduser(target_path)),
        f"{convert_string_to_valid_filename(schema_name)}.msm.project",
    )


def create_schema_project_folder(
    schema_name: str,
    target_path: str,
    copyright_holder: str,
    license: str = None,
    overwrite_existing: bool = False,
    allow_special_chars: bool = False,
    enforce_target_path: bool = False,
    copyrights: list = None,
    year_of_creation=None,
) -> str:
    """Creates a new schema project folder.

    Args:
        schema_name (str): The name of the schema.
        target_path (str): The path to the schema project folder.
        copyright_holder (str): The name of the copyright holder.
        license (str): The license to use for the project.
        overwrite_existing (bool): If the project folder already exists, overwrite it.
        allow_special_chars (bool): If set to True, allows all characters
        enforce_target_path (bool): If set to true, the target_path is created if it does not yet exist.
        copyrights (list): The copyright notices of the project, for a project
            held by more than one copyright holder. When given, it takes the
            place of copyright_holder. See the copyright notices section at the
            top of this file for the format of an entry.
        year_of_creation (str): The year of creation of the copyright, for
            a project that existed before it became an MSM project. Defaults
            to the current year; an entry of copyrights without one gets it.
            Where it lies before the current year, the holders that track
            updates get the current year as the year of their last update.

    Returns:
        The path of the project folder that was created.
    """

    check_mysql_identifier(
        identifier=schema_name, must_be_usable_when_unquoted=not allow_special_chars
    )

    # Checked before anything is written
    year_of_creation = (
        current_year() if year_of_creation in (None, "")
        else check_copyright_year(year_of_creation)
    )
    if copyrights:
        copyrights = [dict(copyright) for copyright in copyrights]
        for copyright in copyrights:
            copyright["yearOfCreation"] = check_copyright_year(
                copyright.get("yearOfCreation") or year_of_creation
            )
            if copyright.get("yearOfLastUpdate"):
                copyright["yearOfLastUpdate"] = check_copyright_year(
                    copyright["yearOfLastUpdate"], "yearOfLastUpdate"
                )

    target_path = os.path.abspath(os.path.expanduser(target_path))
    if not os.path.exists(target_path):
        if enforce_target_path:
            Path(target_path).mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError(
                f"The project folder cannot be created inside the directory `{target_path}` as this path "
                "does not exist."
            )

    # When used in file names, critical characters need to be removed
    schema_file_name = convert_string_to_valid_filename(schema_name)

    project_path = project_folder_path(target_path, schema_name)
    if os.path.exists(project_path):
        if overwrite_existing:
            shutil.rmtree(project_path)
        else:
            raise ValueError(f'The project folder "{project_path}" already exists.')

    os.makedirs(os.path.join(project_path), exist_ok=True)

    # Copy the template project file structure
    template_folder = os.path.join(Path(__file__).parent.parent, "templates")
    shutil.copytree(
        Path(os.path.join(template_folder, "msm.project")),
        project_path,
        dirs_exist_ok=True,
    )

    if not copyrights:
        copyrights = [
            {
                "holder": copyright_holder,
                "yearOfCreation": year_of_creation,
                "tracksUpdates": True,
            }
        ]
    # Creating the project is the first change applied to it
    bump_copyright_years(copyrights)

    project_settings = {
        "copyrights": copyrights,
        # A mirror of the first holder, kept so tools written against the
        # earlier single holder format keep reading the project. The
        # `copyrights` list above is the authoritative one.
        "copyrightHolder": copyrights[0].get("holder"),
        "customLicense": "",
        "license": license,
        # On by default; written so the setting can be found and changed
        "onlineAlter": {"enabled": True},
        "schemaDependencies": [],
        "schemaName": schema_name,
        "schemaFileName": schema_file_name,
        "yearOfCreation": copyrights[0].get("yearOfCreation", year_of_creation),
    }

    # Write the msm.project.json file
    with open(os.path.join(project_path, "msm.project.json"), "w") as f:
        f.write(json.dumps(project_settings, indent=4))

    # Replace placeholders in all markdown files
    files = list(Path(project_path).rglob("*.md"))
    for file in files:
        with open(file, "r") as f:
            script = Template(f.read())
            script = script.substitute(
                {
                    "schema_name": schema_name,
                    "schema_identifier": get_schema_identifier(schema_name),
                }
            )

        # The templates carry this repository's own notices; a generated project
        # gets the notices of its own copyright holders instead. The whole run of
        # notice lines is replaced at once, so the number of notices in the
        # template and in the project do not have to match. A function is used
        # as the replacement, as the holder names are data and must not be read
        # as backreferences.
        notices = render_copyright_notices(project_settings)
        script = COPYRIGHT_BLOCK_REGEX.sub(lambda _: notices, script)

        with open(file, "w") as f:
            f.write(script)

    copy_template_file_and_substitute(
        source_file_path=DEVELOPMENT_SCRIPT_TEMPLATE,
        target_file_path=os.path.join(
            project_path, "development", f"{schema_file_name}_next.sql"
        ),
        substitutions=development_script_substitutions(project_settings, "0.0.1"),
    )

    return project_path


def get_schema_development_file_path(schema_project_path: str = None) -> str:
    schema_file_name = get_project_settings(schema_project_path).get(
        "schemaFileName", None
    )
    if schema_file_name is None:
        raise ValueError(
            f"The settings files of the project `{schema_project_path}` does not contain "
            "a schemaFileName value."
        )

    schema_dev_file_path = os.path.join(
        schema_project_path, "development", f"{schema_file_name}_next.sql"
    )
    if not os.path.exists(schema_dev_file_path):
        raise ValueError(
            f"The MSM project folder does not contain a schema development file `{schema_dev_file_path}`."
        )

    return schema_dev_file_path


def get_schema_development_sections(schema_project_path: str = None) -> dict[str, dict]:
    schema_dev_file_path = get_schema_development_file_path(schema_project_path)

    with open(schema_dev_file_path, "r") as f:
        script = f.read()

    return get_script_sections(script)


def write_schema_development_file(
    schema_project_path: str, sections: dict[str, dict]
) -> None:
    schema_dev_file_path = get_schema_development_file_path(schema_project_path)
    write_sections_to_file(
        file_path=schema_dev_file_path, sections=sections, overwrite_existing=True
    )


def schema_development_version(
    schema_project_path: str = None,
    sections: dict[str, dict] = None,
    new_version: str = None,
    write_to_file: bool = False,
) -> str:
    """Returns the development version of the current schema

    Args:
        schema_project_path: The path to the schema project folder.
        sections: The list of sections of the schema development file.
        new_version: If not None, the given version is set as new development version.
        write_to_file: Whether the development file should be updated. Defaults to False.

    Returns:
        A dict with information about the project
    """
    if sections is None and schema_project_path is None:
        raise ValueError("No sections nor schema_project_path parameters given.")

    if sections is None:
        sections = get_schema_development_sections(
            schema_project_path=schema_project_path
        )

    # Process MSM Section 910: Database Schema Version
    version_section = sections.get("910", None)
    if version_section is None:
        for section_id in sections:
            section = sections[section_id]
            print(
                f"{section_id=}\n{section['full_content']=}\n\n{section['sql_content']=}\n\n\n"
            )
        raise ValueError(
            "The script section `MSM Section 910: Database Schema Version` could not be found."
        )

    section_content = version_section.get("full_content")
    version_values = re.search(
        MSM_SCHEMA_VERSION_VIEW_VALUES, section_content, re.MULTILINE | re.DOTALL
    )
    if version_values is None:
        raise ValueError(
            "The script section `MSM Section 910: Database Schema Version` does not include the "
            "CREATE VIEW statement to create the `msm_schema_version` VIEW."
        )

    # If no new_version is given, return the current one
    if new_version is None:
        return f"{version_values.group(2)}.{version_values.group(3)}.{version_values.group(4)}"

    new_version_list = lib.core.convert_version_str_to_list(new_version)

    # Update the version
    updated_content = (
        section_content[: version_values.start(1)]
        + ", ".join(str(number) for number in new_version_list)
        + section_content[version_values.end(1) :]
    )

    version_section["full_content"] = updated_content

    sections["910"] = version_section

    if write_to_file:
        write_schema_development_file(
            schema_project_path=schema_project_path, sections=sections
        )

    return new_version


def get_schema_development_version(
    schema_project_path: str = None, sections: dict[str, dict] = None
) -> str:
    """Returns the development version of the current schema

    Args:
        schema_project_path: The path to the schema project folder.
        sections: The list of sections of the schema development file.

    Returns:
        A dict with information about the project
    """
    return schema_development_version(
        schema_project_path=schema_project_path, sections=sections
    )


def set_development_version(
    new_version: str,
    schema_project_path: str = None,
    sections: dict = None,
    write_to_file: bool = False,
) -> dict:
    """Sets the development version inside the development/schema_next.sql file

    Args:
        version: The new version to create.
        schema_project_path: The path to the schema project folder.
        sections: The list of sections of the schema development file.
        write_to_file: Whether the development file should be updated. Defaults to False.

    Returns:
        None
    """
    if write_to_file and schema_project_path is not None:
        if update_copyright_years(schema_project_path) and sections:
            # Sections read before the notices moved along would write the
            # old years back: the header is in the first one.
            copyrights = get_project_settings(schema_project_path)["copyrights"]
            first = sections[next(iter(sections))]
            first["full_content"] = refresh_notice_years(first["full_content"], copyrights)
    return schema_development_version(
        schema_project_path=schema_project_path,
        sections=sections,
        new_version=new_version,
        write_to_file=write_to_file,
    )


def get_project_settings(schema_project_path: str) -> dict:
    """Returns the project settings

    Args:
        schema_project_path: The path to the schema project folder.

    Returns:
        A dict with the project settings
    """
    if schema_project_path is None:
        raise ValueError("No schema_project_path given.")

    project_settings_file = os.path.join(schema_project_path, "msm.project.json")
    if not os.path.exists(project_settings_file):
        raise ValueError(
            f"The path {schema_project_path} does not contain a MSM project folder."
        )

    with open(project_settings_file, "r") as f:
        try:
            project_settings = json.loads(f.read())
        except:
            raise ValueError(
                f"The contents of the MSM project settings file `{project_settings_file}` is corrupted."
            )

    # Projects written before the `copyrights` list existed only hold a single
    # copyrightHolder, so the list is derived here and every reader gets it.
    project_settings["copyrights"] = get_project_copyrights(project_settings)

    return project_settings


def get_released_versions(schema_project_path: str) -> list[list[int, int, int]]:
    """Returns the all released version of the database schema

    Args:
        schema_project_path: The path to the schema project folder.

    Returns:
        The list of released database versions as list of list of 3 ints.
    """
    if schema_project_path is None:
        raise ValueError("No schema_project_path given.")

    # Look at all files and get the highest version
    files = []
    for file in next(
        os.walk(os.path.join(schema_project_path, "releases", "versions"))
    )[2]:
        files.append(file)

    versions = []
    for file in files:
        version_match = re.match(r".*?(\d+)\.(\d+)\.(\d+)\.sql", file)
        if version_match is not None:
            file_version = [
                int(version_match.group(1)),
                int(version_match.group(2)),
                int(version_match.group(3)),
            ]
            versions.append(file_version)

    versions.sort()

    return versions


def get_updatable_versions(schema_project_path: str) -> list[list[int, int, int]]:
    """Returns the all version that have update scripts

    Args:
        schema_project_path: The path to the schema project folder.

    Returns:
        The list of updatable database versions as list of list of 3 ints.
    """
    if schema_project_path is None:
        raise ValueError("No schema_project_path given.")

    # Look at all files and get the highest version
    files = []
    for file in next(os.walk(os.path.join(schema_project_path, "releases", "updates")))[
        2
    ]:
        files.append(file)

    versions = []
    versions_to = []
    for file in files:
        version_match = re.match(
            r".*?(\d+)\.(\d+)\.(\d+)_to_(\d+)\.(\d+)\.(\d+)\.sql", file
        )
        if version_match is not None:
            file_version = [
                int(version_match.group(1)),
                int(version_match.group(2)),
                int(version_match.group(3)),
            ]
            versions.append(file_version)
            to_version = [
                int(version_match.group(4)),
                int(version_match.group(5)),
                int(version_match.group(6)),
            ]
            versions_to.append(to_version)

    versions.sort()
    versions_to.sort()

    # Check if there are any missing version updates
    if len(versions) > 0:
        versions_to.insert(0, versions[0])
        del versions_to[-1]

        missing_upgrade_from = None
        missing_upgrade_to = None
        for i in range(len(versions)):
            if versions[i] != versions_to[i]:
                missing_upgrade_from = versions[i]
                missing_upgrade_to = versions_to[i]
            else:
                continue
            break

        if missing_upgrade_from is not None:
            raise Exception(
                "The list of upgrade scripts misses an upgrade step from "
                f"version {missing_upgrade_from} to {missing_upgrade_to}. "
                "Please create the required update script."
            )

    return versions


def get_last_released_version(schema_project_path: str) -> list[int, int, int] | None:
    """Returns the last released version of the database schema

    Args:
        schema_project_path: The path to the schema project folder.

    Returns:
        The last released version or None if no version was released yet
    """
    versions = get_released_versions(schema_project_path)

    if len(versions) > 0:
        return versions[-1]
    else:
        return None


def get_deployment_script_versions(
    schema_project_path: str,
) -> list[list[int, int, int]]:
    """Returns the all deployed version of the database schema

    Args:
        schema_project_path: The path to the schema project folder.

    Returns:
        The list of deployed database versions as list of list of 3 ints.
    """
    if schema_project_path is None:
        raise ValueError("No schema_project_path given.")

    # Look at all files and get the highest version
    files = []
    for file in next(
        os.walk(os.path.join(schema_project_path, "releases", "deployment"))
    )[2]:
        files.append(file)

    versions = []
    for file in files:
        version_match = re.match(r".*?(\d+)\.(\d+)\.(\d+)\.sql", file)
        if version_match is not None:
            file_version = [
                int(version_match.group(1)),
                int(version_match.group(2)),
                int(version_match.group(3)),
            ]
            versions.append(file_version)

    versions.sort()

    return versions


def get_last_deployment_script_version(
    schema_project_path: str,
) -> list[int, int, int] | None:
    """Returns the last deployment version of the database schema

    Args:
        schema_project_path: The path to the schema project folder.

    Returns:
        The version of the last deployment or None if no version was released yet
    """
    versions = get_deployment_script_versions(schema_project_path)

    if len(versions) > 0:
        return versions[-1]
    else:
        return None


def get_project_info(schema_project_path: str) -> dict:
    """Returns information about the project

    Args:
        schema_project_path: The path to the schema project folder.

    Returns:
        A dict with information about the project
    """

    project_info = get_project_settings(schema_project_path)
    project_info["currentDevelopmentVersion"] = get_schema_development_version(
        schema_project_path=schema_project_path
    )
    last_released_version = get_last_released_version(schema_project_path)
    project_info["lastReleasedVersion"] = (
        "%d.%d.%d" % tuple(last_released_version)
        if last_released_version is not None
        else None
    )
    project_info["schemaDevelopmentFilePath"] = get_schema_development_file_path(
        schema_project_path
    )

    return project_info


def sql_content_has_no_statement(
    sql_content: str, pattern: re.Pattern[str] = None
) -> bool:
    """Removes all comments and delimiter statements

    Args:
        section_content: The section content

    Returns:
        True if the section content only contains comments or delimiter statements
    """

    if pattern is None:
        pattern = re.compile(
            MSM_SECTION_REMOVE_COMMENTS_AND_DELIMITERS, re.MULTILINE | re.DOTALL
        )

    return re.sub(pattern, "", sql_content, 0).strip() == ""


def remove_empty_sections(sections: dict, keep_even_if_empty=list[str]) -> None:
    """Removes all empty sections from the given section dict

    Args:
        sections: The section dict with all sections
        keep_even_if_empty: A list of section ids that should kept even if empty

    Returns:
        None
    """

    pattern = re.compile(
        MSM_SECTION_REMOVE_COMMENTS_AND_DELIMITERS, re.MULTILINE | re.DOTALL
    )

    sections_to_remove = []

    for section_id in sections:
        if section_id not in keep_even_if_empty:
            section = sections[section_id]
            full_content = section.get("full_content", "")
            if sql_content_has_no_statement(full_content, pattern):
                sections_to_remove.append(section_id)

    for section_id in sections_to_remove:
        sections.pop(section_id, None)


def prepare_release(
    schema_project_path: str,
    version: str,
    next_version: str,
    allow_to_stay_on_same_version: bool = False,
    overwrite_existing: bool = False,
    lsp_path: str = None,
) -> list[str]:
    """Adds a new release to the schema project

    The schema snapshot of the diagrams (MSM Section 160) is made again from
    the development script first, so a release always holds the one that
    matches its CREATE statements. That needs the MariaDB SQL language
    server's binary, mariadb-lsp; without it, or when a statement cannot be
    read, no release is made.

    Args:
        schema_project_path: The path to the schema project folder.
        version: The new version to create.
        next_version: The next development version.
        allow_to_stay_on_same_version: Whether to allow to stay on the
            same version for further development work
        overwrite_existing: Whether existing files should be overwritten. Defaults to False.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.

    Returns:
        A list of filenames that have been created or changed for the release
    """

    update_copyright_years(schema_project_path)
    project_settings = get_project_settings(schema_project_path)
    schema_name = project_settings.get("schemaName")
    schema_file_name = project_settings.get("schemaFileName")

    last_released_version = get_last_released_version(schema_project_path)
    version_as_ints = lib.core.convert_version_str_to_list(version)

    files_for_release = []

    # Ensure provided version are OK
    if last_released_version is not None:
        if last_released_version > version_as_ints:
            raise ValueError(
                f"The given version {version} is lower than the last released "
                f"version {'%d.%d.%d' % tuple(last_released_version)}."
            )

    if (
        not allow_to_stay_on_same_version
        and lib.core.convert_version_str_to_list(next_version) <= version_as_ints
    ):
        raise ValueError(
            "The next development version needs to be higher than the version for release "
            f"{version}. Please explicitly pass the according flag to stay on the same version."
        )
    elif (
        allow_to_stay_on_same_version
        and lib.core.convert_version_str_to_list(next_version) < version_as_ints
    ):
        raise ValueError(
            "The next development version needs to be at least at the same version as the version for release "
            f"{version}."
        )

    # Refused before the snapshot below changes the development files.
    schema_version_file_path = os.path.join(
        schema_project_path, "releases", "versions", f"{schema_file_name}_{version}.sql"
    )
    if os.path.exists(schema_version_file_path) and not overwrite_existing:
        raise ValueError(
            f"The file {schema_version_file_path} already exists. Please explicitly allow to replace existing files."
        )

    # The release carries the snapshot of its CREATE statements. Like section
    # 910 it changes the development files, which are not listed. (Imported
    # here: lib.modeling imports this module.)
    from msm_plugin.lib import modeling

    modeling.update_schema_definition(schema_project_path, lsp_path=lsp_path)

    # Get the current schema development file sections
    schema_dev_file_path = os.path.join(
        schema_project_path, "development", f"{schema_file_name}_next.sql"
    )
    if not os.path.exists(schema_dev_file_path):
        raise ValueError(
            f"The MSM project folder does not contain a schema development file `{schema_dev_file_path}`."
        )
    with open(schema_dev_file_path, "r") as f:
        schema_dev_script = f.read()
    # Make sure to resolve SOURCE statements with actual content
    schema_dev_script = substitute_source_statements_with_content(
        schema_dev_script, os.path.dirname(schema_dev_file_path)
    )
    schema_dev_expanded_sections = get_script_sections(schema_dev_script)

    # Prepare the schema version template file
    template_folder = os.path.join(Path(__file__).parent.parent, "templates")
    schema_version_template_file_path = os.path.join(
        template_folder, "scripts", "schema_a.b.c.sql"
    )
    if not os.path.exists(schema_version_template_file_path):
        raise ValueError(
            f"The schema release version template file `{schema_version_template_file_path}` does not exist."
        )
    with open(schema_version_template_file_path, "r") as f:
        # Remove the copyright header and replace placeholders
        schema_version_script = Template(strip_template_copyright_header(f.readlines()))
    schema_version_script = schema_version_script.substitute(
        {
            # get_license_text(project_settings=project_settings),
            "license": "License Placeholder",
            "schema_name": schema_name,
            "schema_identifier": get_schema_identifier(schema_name),
            "version_str": version,
            "version_comma_str": ", ".join(str(number) for number in version_as_ints),
        }
    )
    schema_version_sections = get_script_sections(schema_version_script)

    # The development file has to have a version section, set to next_version below
    if "910" not in schema_dev_expanded_sections:
        raise ValueError(
            "The script section `MSM Section 910: Database Schema Version` could not be found in "
            f"`{schema_dev_file_path}`."
        )

    # Loop over all sections of the version file and replace the sections with the ones from the development file.
    # Section 910 is the exception: the development file's states the development version, the template's the
    # released one, which the deployed schema has to report for later updates to apply.
    for section_id in schema_version_sections:
        section = schema_dev_expanded_sections.get(section_id, None)
        if section is not None and section_id != "910":
            schema_version_sections[section_id] = section

    remove_empty_sections(
        sections=schema_version_sections, keep_even_if_empty=["license", "001"]
    )

    # Write the schema version file out
    write_sections_to_file(
        file_path=schema_version_file_path,
        sections=schema_version_sections,
        overwrite_existing=overwrite_existing,
    )
    files_for_release.append(schema_version_file_path)

    # Check if update script is needed and if so, write it out
    if last_released_version is not None:
        last_released_version_str = "%d.%d.%d" % tuple(last_released_version)
        if last_released_version_str != version:
            schema_update_file_path = os.path.join(
                schema_project_path,
                "releases",
                "updates",
                f"{schema_file_name}_{last_released_version_str}_to_{version}.sql",
            )
            copy_template_file_and_substitute(
                source_file_path=os.path.join(
                    template_folder, "scripts", "schema_x.y.z_to_a.b.c.sql"
                ),
                target_file_path=schema_update_file_path,
                substitutions={
                    "license": get_license_text(project_settings=project_settings),
                    "schema_name": schema_name,
                    "schema_identifier": get_schema_identifier(schema_name),
                    "version_from": last_released_version_str,
                    "version_to": version,
                    "version_comma_str": ", ".join(
                        str(number)
                        for number in lib.core.convert_version_str_to_list(version)
                    ),
                },
            )
            files_for_release.append(schema_update_file_path)

            # The changes msm.editSchema kept for this release go into the
            # update script's sections, and the pending script starts over.
            from msm_plugin.lib import editing

            pending_path = editing.move_pending_update(
                schema_project_path, project_settings, schema_update_file_path
            )
            if pending_path:
                files_for_release.append(pending_path)

    # Update the version of the development schema file store in section
    # MSM Section 910: Database Schema Version
    set_development_version(
        new_version=next_version,
        schema_project_path=schema_project_path,
        write_to_file=True,
    )

    return files_for_release


def generate_deployment_script(
    schema_project_path: str, version: str, overwrite_existing: bool = False
) -> str:
    """Generate the deployment script for a release

    Args:
        schema_project_path: The path to the schema project folder.
        version: The version to generate the deployment script for.
        overwrite_existing: Whether existing files should be overwritten. Defaults to False.

    Returns:
        The file name path of the generated script
    """
    update_copyright_years(schema_project_path)
    project_settings = get_project_settings(schema_project_path)
    schema_name = project_settings.get("schemaName")
    schema_file_name = project_settings.get("schemaFileName")

    # Build deployment file path that will be used for output and check that it does not exist
    deployment_file_path = os.path.join(
        schema_project_path,
        "releases",
        "deployment",
        f"{schema_file_name}_deployment_{version}.sql",
    )
    if os.path.exists(deployment_file_path) and not overwrite_existing:
        raise ValueError(
            f"The file {deployment_file_path} already exists. Please explicitly allow to replace existing files."
        )

    # Build the version file path that is used as a source and check that it does exist
    version_file_path = os.path.join(
        schema_project_path, "releases", "versions", f"{schema_file_name}_{version}.sql"
    )

    if not os.path.exists(version_file_path):
        raise ValueError(
            f"The file {version_file_path} does not exist exists. Please make "
            f"sure to prepare the release {version} first."
        )

    # Get the version script file section
    target_version_sections = get_file_sections(version_file_path)

    version_as_ints = lib.core.convert_version_str_to_list(version)

    # Ensure there are no missing updates between 2 released versions
    updatable_versions = get_updatable_versions(schema_project_path)

    # Get the list of released version and build list of updatable versions
    released_versions = get_released_versions(schema_project_path)
    i = 0
    updatable_versions = []
    updatable_versions_sections = {}
    while i + 1 < len(released_versions) and released_versions[i] < version_as_ints:
        version_from = "%d.%d.%d" % tuple(released_versions[i])
        version_to = "%d.%d.%d" % tuple(released_versions[i + 1])

        updatable_versions.append(version_from)
        updatable_versions_sections[version_from] = {
            "version_from": released_versions[i],
            "version_to": released_versions[i + 1],
            "sections": get_file_sections(
                os.path.join(
                    schema_project_path,
                    "releases",
                    "updates",
                    f"{schema_file_name}_{version_from}_to_{version_to}.sql",
                )
            ),
        }
        i += 1

    if len(released_versions) == 0:
        raise ValueError(
            "Please prepare a version for release before generating a deployment script."
        )

    # If there is exactly one released versions yet, use the version SQL script as deployment script
    if len(released_versions) == 1:
        shutil.copyfile(version_file_path, deployment_file_path)
        return deployment_file_path

    # Prepare the schema version template file
    template_folder = os.path.join(Path(__file__).parent.parent, "templates")
    schema_deployment_template_file_path = os.path.join(
        template_folder, "scripts", "schema_deployment_a.b.c.sql"
    )
    if not os.path.exists(schema_deployment_template_file_path):
        raise ValueError(
            f"The schema release version template file `{schema_deployment_template_file_path}` does not exist."
        )
    with open(schema_deployment_template_file_path, "r") as f:
        # Remove the copyright header and replace placeholders
        schema_deployment_script = Template(
            strip_template_copyright_header(f.readlines())
        )
    schema_deployment_script = schema_deployment_script.safe_substitute(
        {
            "license": get_license_text(project_settings=project_settings),
            "schema_name": schema_name,
            "schema_identifier": get_schema_identifier(schema_name),
            "version_target": version,
            "version_comma_str": ", ".join(str(number) for number in version_as_ints),
            "section_130_creation_of_helpers": target_version_sections.get("130", {})
            .get("sql_content", "")
            .strip("\n"),
            "section_140_non_idempotent_schema_objects": indent(
                target_version_sections.get("140", {})
                .get("sql_content", "")
                .strip("\n"),
                "    ",
            ),
            "section_150_idempotent_schema_objects": target_version_sections.get(
                "150", {}
            )
            .get("sql_content", "")
            .strip("\n"),
            "section_160_schema_diagrams": target_version_sections.get("160", {})
            .get("sql_content", "")
            .strip("\n"),
            "section_170_authorization": indent(
                target_version_sections.get("170", {})
                .get("sql_content", "")
                .strip("\n"),
                "    ",
            ),
            "section_180_rest_service_definition": target_version_sections.get(
                "180", {}
            )
            .get("sql_content", "")
            .strip("\n"),
            "section_190_removal_of_helpers": target_version_sections.get("190", {})
            .get("sql_content", "")
            .strip("\n"),
            "updatable_versions": ", ".join(f'"{v}"' for v in updatable_versions),
        }
    )

    matches = re.finditer(
        MSM_LOOP_UPDATABLE_VERSIONS_REGEX,
        schema_deployment_script,
        re.MULTILINE | re.DOTALL,
    )
    # for match_id, match in enumerate(matches, start=1):
    #     print("Match {match_id} was found at {start}-{end}: {match}".format(
    #         match_id=match_id, start=match.start(), end=match.end(), match=match.group()))
    #     for group_id in range(0, len(match.groups())):
    #         group_id = group_id + 1
    #         print("Group {group_id} found at {start}-{end}: {group}".format(group_id=group_id,
    #               start=match.start(group_id), end=match.end(group_id), group=match.group(group_id)))

    for match in reversed(list(matches)):
        loop_content_template = match.group(3)
        loop_content = ""
        needs_indent = match.group(2)
        if needs_indent is None or needs_indent == "":
            needs_indent = 0
        else:
            needs_indent = int(needs_indent)

        for version_from in updatable_versions_sections:
            loop_version_content = loop_content_template
            sections_to_replace = reversed(
                list(re.finditer(MSM_SECTION_PLACEHOLDER_REGEX, loop_content_template))
            )
            for s in sections_to_replace:
                section_id = s.group(1)
                sql_content = (
                    updatable_versions_sections[version_from]["sections"]
                    .get(section_id, {})
                    .get("sql_content", "")
                    .strip("\n")
                )
                sql_content_indent = (
                    indent(sql_content, " " * needs_indent)
                    if needs_indent
                    else sql_content
                )
                sql_content_indent += "\n"

                # If the sql_content has no SQL statements, do not insert
                if sql_content_has_no_statement(sql_content_indent):
                    sql_content_indent = ""

                loop_version_content = (
                    loop_content_template[: s.start()]
                    + sql_content_indent
                    + loop_content_template[s.end() :]
                )

            loop_content += Template(loop_version_content).safe_substitute(
                {
                    "version_from": "%d.%d.%d"
                    % tuple(updatable_versions_sections[version_from]["version_from"]),
                    "version_to": "%d.%d.%d"
                    % tuple(updatable_versions_sections[version_from]["version_to"]),
                }
            )

        # Check if the loop_content is empty (TODO: check if it contains no statements) and if so
        # insert a placeholder
        if (
            len(updatable_versions_sections) > 0
            and loop_content == ""
            and needs_indent > 0
        ):
            loop_content = " " * needs_indent + "DO NONE;\n"

        if sql_content_has_no_statement(loop_content):
            loop_content = ""

        schema_deployment_script = (
            schema_deployment_script[: match.start()]
            + loop_content
            + schema_deployment_script[match.end() :]
        )

    # Remove empty sections
    schema_deployment_script_sections = get_script_sections(schema_deployment_script)
    remove_empty_sections(
        sections=schema_deployment_script_sections,
        keep_even_if_empty=["license", "003"],
    )

    write_sections_to_file(
        file_path=deployment_file_path,
        sections=schema_deployment_script_sections,
        overwrite_existing=overwrite_existing,
    )

    # with open(deployment_file_path, "w") as f:
    #     f.write(schema_deployment_script)

    return deployment_file_path


def get_available_licenses() -> list[str]:
    """Returns the list of available licenses

    Returns:
        The available licenses as a list of strings
    """
    template_folder = os.path.join(Path(__file__).parent.parent, "templates")
    license_path = os.path.join(template_folder, "license")

    files = []
    for file in next(os.walk(license_path))[2]:
        files.append(file[:-4])
    files.sort()

    return files


def substitute_source_statements_with_content(
    script: str, source_absolute_file_path: str
):
    """Returns the script with substituted source statements

    See :mod:`msm_plugin.lib.source`, which also mcp_plugin's
    db.execute_sql_script expands them with.

    Args:
        script: The script content as string
        source_absolute_file_path: The absolute path of the referenced script files

    Returns:
        The script with substituted source statements
    """
    return expand_source_directives(script, source_absolute_file_path)[0]


def get_online_alter_settings(schema_project_path: str, table: str = None) -> dict:
    """The project's online ALTER TABLE setting, and what it gives a table.

    Args:
        schema_project_path: The project folder.
        table: A table to resolve the options of; None for none.

    Returns:
        {defaults: the options msm uses where nothing is set (camelCase
        keys), setting: msm.project.json's `onlineAlter`, normalized
        ({enabled?, <options>?, tables: {...}}), table, enabled: whether it
        is on for the table, options: what the table gets (None where it is
        off or no table is given)}.
    """
    setting = online_alter_lib.read_setting(
        get_project_settings(schema_project_path).get(online_alter_lib.SETTING),
        "msm.project.json",
    )
    options = None
    enabled = None
    if table is not None:
        resolved = online_alter_lib.DeploymentSettings(setting).options_for(table)
        enabled = resolved is not None
        options = resolved.to_dict() if resolved is not None else None
    return {
        "defaults": online_alter_lib.Options().to_dict(),
        "setting": setting,
        "table": table,
        "enabled": enabled,
        "options": options,
    }


def _read_raw_project_settings(schema_project_path: str):
    """msm.project.json's path and content as stored, not get_project_settings'
    copy, which adds what it derives on reading."""
    path = os.path.join(schema_project_path, "msm.project.json")
    get_project_settings(schema_project_path)  # a project, and readable
    with open(path) as f:
        return path, json.load(f)


def _write_online_alter_setting(path: str, raw: dict, setting) -> list:
    """Writes the setting into msm.project.json; None means unchanged."""
    if setting is None:
        return []
    raw[online_alter_lib.SETTING] = setting
    with open(path, "w") as f:
        f.write(json.dumps(raw, indent=4))
    return [path]


def set_online_alter_defaults(schema_project_path: str, settings: dict = None) -> list:
    """Sets the general values of the project's online ALTER TABLE setting
    (msm.project.json, onlineAlter), what the Edit MSM Project dialog's
    Online Schema Change tab writes. The tables' entries stay as they are.

    Args:
        schema_project_path: The project folder.
        settings: {enabled?, maxRebuildBytes?, galeraMaxBytes?, nboLock?,
            maxLockedBytes?, lockWait?, maxRetries?}; it replaces the general
            values, so a key left out takes msm's default again. None or {}
            removes them all.

    Returns:
        The files written; none where nothing changed.

    Raises:
        ValueError: For settings that are no object, that hold `tables`, or
            that online_alter.read_setting refuses.
    """
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("The general settings must be an object, or None to remove them.")
    if settings and "tables" in settings:
        raise ValueError(
            "The general settings cannot hold `tables`; msm.set_online_alter_table "
            "sets a table's entry."
        )
    # Checked as they will be read back
    online_alter_lib.read_setting(settings or {}, "the general settings")

    path, raw = _read_raw_project_settings(schema_project_path)
    return _write_online_alter_setting(
        path, raw, online_alter_lib.with_general(raw.get(online_alter_lib.SETTING), settings)
    )


def set_online_alter_table(schema_project_path: str, table: str, settings: dict = None) -> list:
    """Sets a table's entry of the project's online ALTER TABLE setting
    (msm.project.json, onlineAlter.tables), what the Table Editor's Online
    Alter Table tab writes.

    Args:
        schema_project_path: The project folder.
        table: The table, as named in the scripts.
        settings: {enabled?, maxRebuildBytes?, galeraMaxBytes?, nboLock?,
            maxLockedBytes?, lockWait?, maxRetries?}; None or {} removes the
            table's entry, so it follows the project's general values.

    Returns:
        The files written; none where nothing changed.

    Raises:
        ValueError: For a table name that is empty, or settings
            online_alter.read_setting refuses.
    """
    if not isinstance(table, str) or not table.strip():
        raise ValueError("A table name is needed.")
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("The table's settings must be an object, or None to remove them.")
    # Checked as they will be read back
    online_alter_lib.read_setting({"tables": {table: settings or {}}}, "the table's settings")

    path, raw = _read_raw_project_settings(schema_project_path)
    setting = raw.get(online_alter_lib.SETTING)
    existing = setting.get("tables") if isinstance(setting, dict) else None
    if not settings and table not in (existing or {}):
        return []

    def change(tables):
        if settings:
            tables[table] = dict(settings)
        else:
            tables.pop(table, None)

    return _write_online_alter_setting(
        path, raw, online_alter_lib.with_tables(setting, change)
    )


def get_substitution_definitions(project_settings: dict) -> dict[str, dict]:
    """Returns the substitutions a project declares in msm.project.json

    The "substitutions" setting maps each name usable as /*<msm:name>*/ in the
    project's SQL scripts to its definition: a "default" value (the empty
    string when not given) and an optional "pattern" (a regular expression the
    values have to match in full).

    Args:
        project_settings (dict): The project settings.

    Returns:
        The definitions by substitution name
    """
    definitions = project_settings.get("substitutions", None) or {}
    if not isinstance(definitions, dict):
        raise ValueError(
            "The `substitutions` project setting has to be an object mapping "
            "each substitution name to its definition."
        )
    for name, definition in definitions.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid substitution name `{name}`.")
        if not isinstance(definition, dict):
            raise ValueError(
                f"The definition of the substitution `{name}` has to be an object."
            )
    return definitions


def resolve_substitutions(
    project_settings: dict, substitutions: dict | None = None
) -> dict[str, str]:
    """Returns the value of every substitution the project declares

    The given values override the declared defaults.

    Args:
        project_settings (dict): The project settings.
        substitutions (dict): The values to use, e.g. {"schema_prefix": "acme_"}.

    Returns:
        The values by substitution name
    """
    definitions = get_substitution_definitions(project_settings)
    values = {
        name: str(definition.get("default", ""))
        for name, definition in definitions.items()
    }

    for name, value in (substitutions or {}).items():
        if name not in definitions:
            declared = ", ".join(sorted(definitions)) or "none"
            raise ValueError(
                f"The substitution `{name}` is not declared by the project "
                f"(declared: {declared})."
            )
        if not isinstance(value, str):
            raise ValueError(f"The value of the substitution `{name}` has to be a string.")
        values[name] = value

    for name, value in values.items():
        if not re.fullmatch(MSM_SUBSTITUTION_VALUE_REGEX, value):
            raise ValueError(
                f"Invalid value `{value}` for the substitution `{name}`: only "
                "letters, digits and _ are allowed."
            )
        pattern = definitions[name].get("pattern", None)
        if pattern is not None and not re.fullmatch(pattern, value):
            raise ValueError(
                f"Invalid value `{value}` for the substitution `{name}`: it has "
                f"to match {pattern}."
            )
    return values


def apply_substitutions(script: str, values: dict[str, str]) -> str:
    """Replaces the /*<msm:name>*/ placeholders of a script with their values

    Args:
        script (str): The SQL script.
        values (dict): The values by substitution name, see resolve_substitutions().

    Returns:
        The script with every placeholder replaced
    """

    def replace(match):
        name = match.group(1)
        if name not in values:
            raise ValueError(
                f"The script uses the substitution /*<msm:{name}>*/ that the "
                "project does not declare."
            )
        return values[name]

    return re.sub(MSM_SUBSTITUTION_REGEX, replace, script)


def get_schema_identifier(schema_name: str) -> str:
    """Returns the schema name as it is written into the SQL scripts

    A name with substitution placeholders, e.g.
    /*<msm:schema_prefix>*/my_schema, is written as is: the placeholders only
    work outside of quotes, so the rest of the name has to be usable as an
    unquoted identifier. Any other name is quoted with backticks.

    Args:
        schema_name (str): The schemaName of the project settings.

    Returns:
        The schema name for SQL scripts
    """
    if re.search(MSM_SUBSTITUTION_REGEX, schema_name) is None:
        return lib.core.quote_ident(schema_name)

    plain = re.sub(MSM_SUBSTITUTION_REGEX, "", schema_name)
    if not re.fullmatch(r"[A-Za-z0-9_]+", plain):
        raise ValueError(
            f"The schema name `{schema_name}` uses substitutions, so the rest of "
            "it may only contain letters, digits and _."
        )
    return schema_name


def get_effective_schema_name(
    project_settings: dict, substitutions: dict | None = None
) -> str:
    """Returns the name of the schema a deployment creates or updates

    That is the schemaName of the project settings with its substitution
    placeholders replaced.

    Args:
        project_settings (dict): The project settings.
        substitutions (dict): The values to use, see resolve_substitutions().

    Returns:
        The schema name
    """
    schema_name = project_settings.get("schemaName", None)
    if schema_name is None:
        raise ValueError("The schema name could not be read from the project settings.")

    name = apply_substitutions(
        schema_name, resolve_substitutions(project_settings, substitutions)
    )
    if len(name) > 64:
        raise ValueError(
            f"The schema name `{name}` is longer than 64 characters."
        )
    return name


def get_schema_name(schema_project_path: str, substitutions: dict | None = None):
    """Gets the schema name from the project settings

    The substitution placeholders of the name are replaced with the given
    values or their defaults, see get_effective_schema_name().

    Args:
        schema_project_path (str): The path to the schema project.
        substitutions (dict): The values to use for the substitutions.

    Returns:
        The schema name
    """
    return get_effective_schema_name(
        get_project_settings(schema_project_path), substitutions
    )


def get_schema_exists(
    session: object, schema_project_path: str = None, schema_name: str = None
) -> bool:
    """Checks whether the given schema exists

    Either the schema_project_path or the schema_name can be given.

    Args:
        session (object): The database session to use.
        schema_project_path (str): The path to the schema project.
        schema_name (str): The name of the schema

    Returns:
        True if the schema exists
    """
    if schema_name is None:
        schema_name = get_schema_name(schema_project_path)

    return (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as schema_count FROM information_schema.SCHEMATA "
            "WHERE SCHEMA_NAME = ?"
        )
        .exec(session, [schema_name])
        .first["schema_count"]
        == 1
    )


def get_schema_is_managed(
    session: object, schema_project_path: str = None, schema_name: str = None
) -> bool:
    """Checks whether the given schema is managed

    Either the schema_project_path or the schema_name can be given.

    Args:
        session (object): The database session to use.
        schema_project_path (str): The path to the schema project.
        schema_name (str): The name of the schema

    Returns:
        True if the schema is managed by MSM
    """
    if schema_name is None:
        schema_name = get_schema_name(schema_project_path)

    return (
        lib.core.MsmDbExec(
            "SELECT COUNT(*) as table_count FROM information_schema.TABLES "
            'WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND TABLE_TYPE = "VIEW"'
        )
        .exec(session, [schema_name, "msm_schema_version"])
        .first["table_count"]
        == 1
    )


def get_schema_version(
    session: object, schema_project_path: str = None, schema_name: str = None
) -> str | None:
    """Returns the current version of the schema

    Either the schema_project_path or the schema_name can be given.

    Args:
        session (object): The database session to use.
        schema_project_path (str): The path to the schema project.
        schema_name (str): The name of the schema

    Returns:
        True if the schema is managed by msm
    """
    if schema_name is None:
        schema_name = get_schema_name(schema_project_path)

    if get_schema_exists(session=session, schema_name=schema_name):
        schema_version = (
            lib.core.MsmDbExec(
                "SELECT CONCAT(major, '.', minor, '.', patch) AS version "
                f"FROM {lib.core.quote_ident(schema_name)}.`msm_schema_version`"
            )
            .exec(session)
            .first["version"]
        )
    else:
        schema_version = None

    return schema_version


def _deployment_error(error: Exception, message: str) -> Exception:
    """The error a failed deployment raises: the message given, and the
    type of an update that needs an external tool kept, so a caller can
    tell it from other failures."""
    if isinstance(error, update_steps.NeedsExternalToolError):
        return update_steps.NeedsExternalToolError(message)
    return Exception(message)


def _update_steps_handler(session, script, schema_name, schema_version, settings):
    """The statement handler that runs an update's steps in msm instead of
    `CALL msm_create_or_update()` (lib/update_steps.py; script is the
    deployment script, its substitutions applied), so the ALTER TABLE
    statements run online; None where the script runs as written: a new
    schema, online ALTER TABLE off for every table, or a script generated
    before the steps were marked."""
    if schema_version is None or not settings.any_enabled():
        return None
    if not update_steps.read_steps(script):
        lib.core.write_to_msm_schema_update_log(
            "INFO",
            "The deployment script marks no update steps (generated by an older "
            "msm): its ALTER TABLE statements run as written.",
        )
        return None

    def handle(statement: str) -> bool:
        if not update_steps.is_create_or_update_call(statement):
            return False
        try:
            update_steps.run_update(
                session,
                script,
                schema_name,
                schema_version,
                settings,
                lib.core.write_to_msm_schema_update_log,
            )
        except update_steps.NeedsExternalToolError:
            raise
        except online_alter_lib.OnlineAlterError as e:
            lib.core.write_to_msm_schema_update_log("ERROR", str(e))
            raise
        return True

    return handle


def deploy_schema(
    session: object,
    schema_project_path: str,
    version: str = None,
    backup_directory: str = None,
    backup: bool = False,
    substitutions: dict = None,
    online_alter=None,
) -> str:
    """Deploys the database schema

    Deploys the given version of the database schema. If no version is given,
    the latest available version will be deployed.

    If an existing schema version is upgraded and backups are enabled, a dump
    of that schema is created first, and is loaded back should the update fail.
    Without backups the schema is left as the failed update leaves it, so no
    dump is written and nothing is restored.

    A log will be written during the update.

    Args:
        session (object): The database session to use.
        schema_project_path (str): The path to the schema project.
        version (str): The version to deploy.
        backup_directory (str): The directory to be used for backups
        backup (bool): Whether to dump an existing schema before updating it,
            so it can be restored if the update fails. Defaults to False.
        substitutions (dict): The values of the substitutions the project
            declares, e.g. {"schema_prefix": "acme_"}. Every /*<msm:name>*/
            in the scripts and the schemaName is replaced with the value
            before the scripts run; names not given use their defaults.
        online_alter (bool | dict): This deployment's online ALTER TABLE
            setting, over the project's `onlineAlter` (same format: true,
            false, or {enabled, maxRebuildBytes, galeraMaxBytes, nboLock,
            maxLockedBytes, lockWait, maxRetries, tables: {<table>: {...}}}). For a table the
            most specific value wins, and at the same level this one.

    Returns:
        None
    """
    project_settings = get_project_settings(schema_project_path)
    if project_settings.get("schemaName", None) is None:
        err_msg = (
            f"The project settings of `{schema_project_path}` could not be " "read."
        )
        lib.core.write_to_msm_schema_update_log("ERROR", err_msg)
        raise ValueError(err_msg)
    try:
        substitution_values = resolve_substitutions(project_settings, substitutions)
        schema_name = get_effective_schema_name(project_settings, substitutions)
        online_alter_settings = online_alter_lib.deployment_settings(
            project_settings, online_alter
        )
    except ValueError as e:
        lib.core.write_to_msm_schema_update_log("ERROR", str(e))
        raise
    schema_file_name = project_settings.get("schemaFileName", None)

    released_versions = get_released_versions(schema_project_path=schema_project_path)
    deployment_script_versions = get_deployment_script_versions(
        schema_project_path=schema_project_path
    )

    # Check if there are actually any released versions of the schema
    if len(released_versions) == 0:
        err_msg = (
            f"There are no versions of the schema `{schema_name}` that have "
            "been released yet."
        )
        lib.core.write_to_msm_schema_update_log("ERROR", err_msg)
        raise Exception(err_msg)

    # Check if there is a difference in released versions and deployment scripts
    if released_versions != deployment_script_versions:
        err_msg = (
            "Deployment script(s) missing. Please generate deployment "
            "scripts for all released versions first."
        )
        lib.core.write_to_msm_schema_update_log("ERROR", err_msg)
        raise Exception(err_msg)

    # If a specific version is requested, ensure that there is a deployment
    # script for this version
    if version is None:
        version = "%d.%d.%d" % tuple(deployment_script_versions[-1])
    elif version not in map(
        lambda v: "%d.%d.%d" % tuple(v), deployment_script_versions
    ):
        err_msg = (
            f"Deployment or update of database schema `{schema_name}` using "
            f"version {version} requested but there is no deployment script "
            "available for this version."
        )
        lib.core.write_to_msm_schema_update_log("ERROR", err_msg)
        raise ValueError(err_msg)

    # Taken before the schema is looked at, and held until the deployment
    # and its cleanup are done: read before the lock, a deployment that
    # waited for another one would take the schema the other one just
    # created as missing, run the script on it again, and drop it if that
    # fails. Another deployment holding the lock is a reason to stop.
    with lib.core.msm_update_lock(session, lock_name_suffix=schema_name):
        # Check if the schema already exists
        schema_exists = get_schema_exists(session=session, schema_name=schema_name)

        # Check if the schema is actually managed by MSM, and if so, get the version
        schema_managed = False
        schema_version = None
        if schema_exists:
            schema_managed = get_schema_is_managed(session=session, schema_name=schema_name)

            if schema_managed:
                schema_version = get_schema_version(
                    session=session, schema_name=schema_name
                )

        if schema_exists and not schema_managed:
            err_msg = (
                f"Deployment or update of database schema `{schema_name}` using "
                f"version {version} requested but the schema is not managed by "
                "MSM."
            )
            lib.core.write_to_msm_schema_update_log("ERROR", err_msg)
            raise Exception(err_msg)

        # If the requested version already matches the version, exit since there is
        # nothing to do
        if schema_version is not None and schema_version == version:
            info_msg = (
                f"Deployment or update of database schema `{schema_name}` using "
                f"version {version} requested but the schema is already on the "
                "requested version. No changes performed."
            )
            lib.core.write_to_msm_schema_update_log("INFO", info_msg)
            return info_msg

        # Check if the current version of the schema is in the list of versions
        # that can be upgraded by the deployment scripts
        if schema_version is not None:
            updatable_versions = get_updatable_versions(schema_project_path)
            updatable_versions_str = map(
                lambda v: "%d.%d.%d" % tuple(v), updatable_versions
            )

            if (
                len(updatable_versions) > 0
                and lib.core.convert_version_str_to_list(schema_version)
                > updatable_versions[-1]
            ):
                info_msg = (
                    f"The database schema `{schema_name}` is on a newer version "
                    f"{schema_version} than shipped with this project (version "
                    f"{'%d.%d.%d' % tuple(updatable_versions[-1])}). No changes "
                    "performed."
                )
                lib.core.write_to_msm_schema_update_log("INFO", info_msg)
                return info_msg

            if not schema_version in updatable_versions_str:
                err_msg = (
                    f"Update of database schema `{schema_name}` to version "
                    f"{version} requested but the version {schema_version} cannot "
                    "be updated."
                )
                lib.core.write_to_msm_schema_update_log("ERROR", err_msg)
                raise Exception(err_msg)

        # Log start of the
        if not schema_exists:
            lib.core.write_to_msm_schema_update_log(
                "INFO",
                f"Starting deployment of database schema `{schema_name}` using "
                f"version {version} ...",
            )
        else:
            lib.core.write_to_msm_schema_update_log(
                "INFO",
                f"Starting update of database schema `{schema_name}` version "
                f"{schema_version} to version {version} ...",
            )

        # Perform dump if the schema exists and backups are enabled
        backup_available = False
        # Left as if local_infile was already enabled, so nothing tries to restore
        # it when no dump is taken and the option is therefore never touched.
        original_local_infile = True
        if schema_exists and backup:
            lib.core.write_to_msm_schema_update_log(
                "INFO",
                f"Preparing dump of `{schema_name}` version "
                f"{schema_version} in order to be roll back in case of an error.",
            )

            if backup_directory is None:
                backup_directory = os.path.join(
                    lib.core.get_msm_plugin_data_path(),
                    "backups",
                    f"{convert_string_to_valid_filename(schema_name)}_backup_"
                    f"{schema_version}",
                )
                # If that directory already exists, keep appending counter until
                # a new directory is found
                i = 2
                candidate = backup_directory
                while os.path.exists(candidate):
                    candidate = f"{backup_directory}_{i}"
                    i += 1
                backup_directory = candidate

            os.makedirs(backup_directory, exist_ok=True)

            # Ensure that the dump can be read back in case of a failure by setting
            # local_infile to 1
            # cSpell:ignore infile
            row = (
                lib.core.MsmDbExec("SELECT @@local_infile as local_infile")
                .exec(session)
                .first
            )
            original_local_infile = row and int(row["local_infile"]) == 1
            if not original_local_infile:
                try:
                    lib.core.write_to_msm_schema_update_log(
                        "INFO",
                        "Enabling local_infile option in order to be able to load "
                        "back the schema dump in case of an update error...",
                    )
                    lib.core.MsmDbExec("SET GLOBAL local_infile=1").exec(session)
                except:
                    err_msg = (
                        "Failed to enable the local_infile option. Please execute "
                        "SET PERSIST GLOBAL local_infile=1; on the MariaDB Server."
                    )
                    lib.core.write_to_msm_schema_update_log("ERROR", err_msg)

                    raise Exception(err_msg)

            lib.core.write_to_msm_schema_update_log(
                "INFO", f"Creating dump of `{schema_name}` version {schema_version} ..."
            )
            # The dump runs on the given session, which is the shell's global
            # session only for the call
            lib.core.run_util(
                session,
                "dump_schemas",
                [schema_name],
                f"file://{backup_directory}",
                options={
                    "skipUpgradeChecks": True,
                    "showProgress": False,
                },
            )

            backup_available = True

        # Run deployment script
        deployment_file_path = os.path.join(
            schema_project_path,
            "releases",
            "deployment",
            f"{schema_file_name}_deployment_{version}.sql",
        )
        try:
            # Read once: the handler runs the update's steps from the same text
            with open(deployment_file_path) as f:
                deployment_script = apply_substitutions(f.read(), substitution_values)
            lib.core.execute_msm_sql_script(
                session=session,
                sql_script=deployment_script,
                script_name=deployment_file_path,
                take_lock=False,
                statement_handler=_update_steps_handler(
                    session,
                    deployment_script,
                    schema_name,
                    schema_version,
                    online_alter_settings,
                ),
            )

            if not schema_exists:
                info_msg = (
                    f"Deployment of `{schema_name}` version "
                    f"{version} completed successfully."
                )
            else:
                if not original_local_infile:
                    lib.core.MsmDbExec("SET GLOBAL local_infile=0").exec(session)
                    lib.core.write_to_msm_schema_update_log(
                        "INFO", "Restored local_infile option."
                    )

                info_msg = (
                    f"Completed the update of `{schema_name}` version "
                    f"{schema_version} to {version} successfully."
                )

            lib.core.write_to_msm_schema_update_log("INFO", info_msg)

            # Remove the backup directory as it is no longer needed
            if backup_available:
                shutil.rmtree(backup_directory)

            return info_msg
        except Exception as e:
            # Drop the schema after a failed update, but only if it can be restored
            # from a dump afterwards, or if it did not exist before this deployment
            # and is therefore this deployment's to clean up. Dropping it without a
            # backup to load back would discard the very data the update was
            # applied to.
            if backup_available or not schema_exists:
                try:
                    lib.core.MsmDbExec(
                        f"DROP SCHEMA IF EXISTS {lib.core.quote_ident(schema_name)}"
                    ).exec(session)
                except:
                    pass

            # Restore the backup if available
            if backup_available:
                try:
                    lib.core.run_util(
                        session,
                        "load_dump",
                        f"file://{backup_directory}",
                        options={
                            "showMetadata": False,
                            "showProgress": False,
                            "ignoreVersion": True,
                        },
                    )

                    if not original_local_infile:
                        lib.core.MsmDbExec("SET GLOBAL local_infile=0").exec(session)
                        lib.core.write_to_msm_schema_update_log(
                            "INFO", "Restored local_infile option."
                        )

                    # Remove the backup directory as it is no longer needed
                    shutil.rmtree(backup_directory)
                except Exception as e_dump_load:
                    err_str = (
                        "An error occurred while updating the database schema "
                        f"`{schema_name}` to version {version}. The schema could "
                        f"not be restored back to version {schema_version}. {e} "
                        f"{e_dump_load}"
                    )
                    lib.core.write_to_msm_schema_update_log("ERROR", err_str)

                    raise _deployment_error(e, err_str) from e

                err_str = (
                    "An error occurred while updating the database schema "
                    f"`{schema_name}` to version {version}. The schema has been "
                    f"restored back to version {schema_version}. {e}"
                )
                lib.core.write_to_msm_schema_update_log("ERROR", err_str)

                raise _deployment_error(e, err_str) from e
            else:
                if not schema_exists:
                    err_str = f"Deploying the database schema `{schema_name}` failed. {e}"
                    lib.core.write_to_msm_schema_update_log("ERROR", err_str)

                    raise _deployment_error(e, err_str) from e
                else:
                    err_str = (
                        "An error occurred while updating the database schema "
                        f"`{schema_name}` to version {version}. {e}"
                    )
                    lib.core.write_to_msm_schema_update_log("ERROR", err_str)

                    raise _deployment_error(e, err_str) from e


def execute_msm_sql_script(
    session, sql_script: str = None, script_name: str = None, sql_file_path: str = None
):
    """
    Execute a SQL script on the MariaDB Server and log the progress in the
    MSM log file.

    The SQL script can be provided as a string, or as a path to a file.

    Args:
        session (object): The MariaDB session to use.
        sql_script (str): The SQL script to execute.
        script_name (str): The name of the SQL script.
        sql_file_path (str): The path to the SQL script file.

    Returns:
        None
    """

    lib.core.execute_msm_sql_script(
        session,
        sql_script=sql_script,
        script_name=script_name,
        sql_file_path=sql_file_path,
    )
