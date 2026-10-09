# Copyright (c) 2021, 2026, Oracle and/or its affiliates.
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

from mrs_plugin.lib import core

import base64
import os
import re
import json
import pathlib
import datetime

# The regex below require that all comments and strings have been blanked before

# Regex to match 9 levels of matching curly brackets { }
MATCHING_CURLY_BRACKETS_REGEX = r"({(?:(?:{(?:(?:{(?:(?:{(?:(?:{(?:(?:{(?:(?:{(?:(?:{(?:(?:{[^}{]*})|[^}{])*})|[^}{])*})|[^}{])*})|[^}{])*})|[^}{])*})|[^}{])*})|[^}{])*})|[^}{])*})"

# Regex to match 9 levels of matching square brackets { }
MATCHING_SQUARE_BRACKETS_REGEX = r"(\[(?:(?:\[(?:(?:\[(?:(?:\[(?:(?:\[(?:(?:\[(?:(?:\[(?:(?:\[(?:(?:\[[^\]\[]*\])|[^\]\[])*\])|[^\]\[])*\])|[^\]\[])*\])|[^\]\[])*\])|[^\]\[])*\])|[^\]\[])*\])|[^\]\[])*\])|[^\]\[])*\])"

# Regex to match MRS schema decorator and class content
TS_SCHEMA_DECORATOR_REGEX = (
    r"@Mrs\.(schema|module)\s*\(\s*{(.*?)}\)\s*class\s*([\w$]+)\s*"
    + MATCHING_CURLY_BRACKETS_REGEX
)

# Regex to match decorator parameters represented as values in a dict with stripped curly brackets { }
# e.g. name: "mrs_notes_scripts", enabled: true, triggerType: MrsScriptFunctionType.BeforeUpdate,
TS_DECORATOR_PROPS_REGEX = (
    r"\s*(\w*)\s*:\s*((\"[^\"\\]*(?:\\.[^\"\\]*)*\")|(\'[^\'\\]*(?:\\.[^\'\\]*)*\')|(\`[^\`\\]*(?:\\.[^\`\\]*)*\`)|"
    + MATCHING_CURLY_BRACKETS_REGEX
    + "|"
    + MATCHING_SQUARE_BRACKETS_REGEX
    + "|"
    + r"([\w\.]*))"
)

# Regex to match MRS script/trigger decorator and function content
TS_SCRIPT_DECORATOR_REGEX = (
    r"@Mrs\.(script|trigger)\s*\(\s*{(.*?)}\s*\)\s*public\s*static\s*(async)?\s*([\w\d_]+)\s*\((.*?)\)\s*:\s*(.*?)\s*"
    + MATCHING_CURLY_BRACKETS_REGEX
)

# Regex to match each parameter + type
TS_SCRIPT_PARAMETERS_REGEX = r"\s*(.*?)(\?)?\s*(:\s*(.*?)\s*)?(=\s*(.*?))?,"

# Regex to remove Promise<> from the result type
TS_SCRIPT_RESULT_REMOVE_PROMISE_REGEX = r"Promise\s*<(.*?)>\s*$"

# Regex to match TS interface definitions
TS_INTERFACE_REGEX = (
    r"export\s+interface\s+(.+?)\s+(extends\s+(.+?))?\s*"
    + MATCHING_CURLY_BRACKETS_REGEX
)

# Regex to match TS interface fields
TS_INTERFACE_FIELDS_REGEX = (
    r"\s*(readonly\s+)?(\[\s*(.+?)\s*:\s*(.+?)\s*\]|.+?)(\?)?\s*:\s*(.+?)\s*[,;]"
)


def query_content_sets(
    session,
    content_set_id: bytes = None,
    service_id: bytes = None,
    request_path=None,
    include_enable_state=None,
):
    """Gets a specific MRS content_set

    Args:
        session (object): The database session to use.
        service_id: The id of the service
        request_path (str): The request_path of the content_set
        content_set_id: The id of the content_set
        include_enable_state (bool): Only include items with the given
            enabled state

    Returns:
        The schema as dict or None on error in interactive mode
    """
    if request_path and not request_path.startswith("/"):
        raise Exception("The request_path has to start with '/'.")

    # Build SQL based on which input has been provided
    sql = """
        SELECT cs.id, cs.service_id, cs.request_path, cs.requires_auth,
            cs.enabled, cs.comments, cs.options,
            CONCAT(h.name, se.url_context_root) AS host_ctx,
            cs.content_type
        FROM `mysql_rest_service_metadata`.`content_set` cs
            LEFT OUTER JOIN `mysql_rest_service_metadata`.`service` se
                ON se.id = cs.service_id
            LEFT JOIN `mysql_rest_service_metadata`.`url_host` h
                ON se.url_host_id = h.id
        """
    params = []
    wheres = []
    if service_id:
        wheres.append("cs.service_id = ?")
        params.append(service_id)
    if request_path:
        wheres.append("cs.request_path = ?")
        params.append(request_path)
    if content_set_id:
        wheres.append("cs.id = ?")
        params.append(content_set_id)
    if include_enable_state is not None:
        wheres.append("cs.enabled = ?")
        params.append("TRUE" if content_set_id else "FALSE")

    sql += core._generate_where(wheres)

    return core.MrsDbExec(sql, params).exec(session).items


def get_content_set(
    session,
    service_id: bytes | None = None,
    request_path=None,
    content_set_id: bytes | None = None,
) -> dict | None:
    """Gets a specific MRS content_set

    Args:
        session (object): The database session to use.
        service_id: The id of the service
        request_path (str): The request_path of the content_set
        content_set_id: The id of the content_set

    Returns:
        The schema as dict or None on error in interactive mode
    """
    if request_path and not request_path.startswith("/"):
        raise Exception("The request_path has to start with '/'.")

    # Build SQL based on which input has been provided
    result = query_content_sets(
        session=session,
        content_set_id=content_set_id,
        service_id=service_id,
        request_path=request_path,
    )
    return result[0] if result else None


def blank_js_comments(s, blank_char=" "):
    pattern = r"(\".*?(?<!\\)\"|\'.*?(?<!\\)\')|(/\*.*?\*/|//[^\r\n]*$)"
    regex = re.compile(pattern, re.MULTILINE | re.DOTALL)
    cleaner_regex = re.compile(r"[^\n]*", re.MULTILINE | re.DOTALL)

    # def _replacer(match):
    #     if match.group(2) is not None:
    #         # Replace multi-line comments with an empty comment
    #         if match.group(2).count("\n") > 0:
    #             return "/*" + "\n" * match.group(2).count("\n") + "*/"
    #         return ""
    #     else:
    #         return match.group(1)

    def _replacer(match: re.Match):
        if match.group(2) is not None:
            # Blank single line comments
            if match.group(2).startswith("//"):
                return "//" + " " * (match.end(2) - match.start(2) - 2)

            # For multi-line strings, ensure to keep line breaks in place. Replace all other characters.
            def __replacer(match2: re.Match):
                blanked_string = blank_char * (match2.end() - match2.start())

                return blanked_string

            return "/*" + cleaner_regex.sub(__replacer, match.group())[2:-2] + "*/"
        else:
            return match.group(1)

    return regex.sub(_replacer, s)


def blank_quoted_js_strings(s, blank_char=" "):
    # Blank strings with all allowed string quotes in JS
    s = blank_quoted_strings(s, '"', r"\"", blank_char)
    s = blank_quoted_strings(s, "'", r"\'", blank_char)
    s = blank_quoted_strings(s, "`", r"\`", blank_char)

    return s


def blank_quoted_strings(s, quote_char, quote_r_char, blank_char=" "):
    # Build the pattern like this, showcasing it for \": r"\"[^\"\\]*(?:\\.[^\"\\]*)*\""
    pattern = (
        quote_r_char
        + r"[^"
        + quote_r_char
        + r"\\]*(?:\\.[^"
        + quote_r_char
        + r"\\]*)*"
        + quote_r_char
    )
    regex = re.compile(pattern, re.MULTILINE | re.DOTALL)
    cleaner_regex = re.compile(r"[^\n]*", re.MULTILINE | re.DOTALL)

    def _replacer(match: re.Match):
        def __replacer(match2: re.Match):
            blanked_string = blank_char * (match2.end() - match2.start())

            return blanked_string

        return (
            quote_char + cleaner_regex.sub(__replacer, match.group())[1:-1] + quote_char
        )

    # def _replacer(match: re.Match):
    #     print(f"{match.group()=}")

    #     if "\n" in match.group():
    #         linebreaks = match.group().count("\n")
    #         replace_with = quote_char + " " * (match.end() - match.start() - 2 - linebreaks) + "\n" * linebreaks + quote_char
    #     else:
    #         replace_with = quote_char + " " * (match.end() - match.start() - 2) + quote_char

    #     print(f"{replace_with=}")
    #     return replace_with

    return regex.sub(_replacer, s)


def convert_ignore_list_to_regex_pattern(ignore_list):
    ignore_patterns = []
    for pattern in ignore_list.split(","):
        ignore_patterns.append(
            pattern.strip()
            .replace("\\", "/")
            .replace(".", "\\.")
            .replace("*", ".*")
            .replace("?", ".")
        )
    if len(pattern) > 0:
        return re.compile(
            "(" + ")|(".join(ignore_patterns) + ")", flags=re.MULTILINE | re.DOTALL
        )
    return None


def get_folder_mrs_scripts_language(path, ignore_list):
    full_ignore_pattern = convert_ignore_list_to_regex_pattern(ignore_list)

    path = os.path.expanduser(path)

    for root, dirs, files in os.walk(path):
        for file in files:
            fullname = os.path.join(root, file)

            # If the filename matches the ignore list, ignore the file
            if full_ignore_pattern is not None and re.match(
                full_ignore_pattern, fullname.replace("\\", "/")
            ):
                continue

            # Detect TypeScript
            if (fullname.endswith(".mts") or fullname.endswith(".ts")) and not (
                fullname.endswith(".spec.mts")
                or fullname.endswith(".spec.ts")
                or fullname.endswith(".d.ts")
            ):

                # Read the file content
                with open(fullname, "r") as f:
                    code = f.read()

                    # Clear TypeScript comments and strings for regex matching
                    code_cleared = blank_quoted_js_strings(blank_js_comments(code))

                    # Search for SCHEMA_DECORATOR
                    match = re.search(
                        TS_SCHEMA_DECORATOR_REGEX,
                        code_cleared,
                        re.MULTILINE | re.DOTALL,
                    )

                    if match is not None:
                        return "TypeScript"

    return None


def get_decorator_param_value(param_value):
    if (
        param_value.startswith('"')
        or param_value.startswith("'")
        or param_value.startswith("`")
    ):
        param_value = param_value[1:-1]
    elif param_value.startswith("{") or param_value.startswith("["):
        # Make sure to match on a version of param_value with blanked out strings
        param_value_blanked = blank_quoted_js_strings(param_value)

        # Remove comma on last item if present
        bracket_char = r"}" if param_value.startswith("{") else r"]"

        # Instead of a simple re.sub() perform the replacement on the original param_value, not the one with
        # blanked strings. Also, do the replacement from bottom up, since the chars are removed and the matched
        # positions would get out of sync with the original param_value
        matches = re.finditer(
            r",\s*" + bracket_char, param_value_blanked, re.MULTILINE | re.DOTALL
        )
        for match in reversed(list(matches)):
            param_value = (
                param_value[0 : match.start()]
                + bracket_char
                + param_value[match.end() :]
            )

        # since the original param_value was changed, get a new blanked version
        param_value_blanked = blank_quoted_js_strings(param_value)

        # Add quotes to keys
        matches = re.finditer(
            r"(?<={|,)\s*([a-zA-Z][a-zA-Z0-9]*)(?=:)",
            param_value_blanked,
            re.MULTILINE | re.DOTALL,
        )
        for match in reversed(list(matches)):
            param_value = (
                param_value[0 : match.start(1)]
                + '"'
                + param_value[match.start(1) : match.end(1)]
                + '"'
                + param_value[match.end(1) :]
            )

        try:
            param_value = json.loads(param_value)
        except:
            pass
    else:
        param_value = param_value.strip()
        if param_value.lower() == "true":
            param_value = True
        elif param_value.lower() == "false":
            param_value = False
        else:
            try:
                param_value = float(param_value)
            except:
                pass

    return param_value


def get_decorator_properties(code, code_cleared, start, end):
    props = []
    matches = re.finditer(
        TS_DECORATOR_PROPS_REGEX, code_cleared[start:end], re.MULTILINE | re.DOTALL
    )

    # for match_id, match in enumerate(matches, start=1):
    # print("Match {matchNum} was found at {start}-{end}: {match}".format(
    #     matchNum=match_id, start=match.start(), end=match.end(), match=match.group()))
    # for groupNum in range(0, len(match.groups())):
    #     groupNum = groupNum + 1
    #     print("Group {groupNum} found at {start}-{end}: {group}".format(groupNum=groupNum, start=match.start(
    #         groupNum), end=match.end(groupNum), group=match.group(groupNum)))

    for match in matches:
        prop_value = code[start + match.start(2) : start + match.end(2)]
        props.append(
            {
                "name": match.group(1),
                "value": get_decorator_param_value(prop_value),
            }
        )

    return props


def get_function_params(code, code_cleared, start, end):
    params = []
    # Add a trailing , to allow for an easier regex
    params_string = code_cleared[start:end] + ","
    matches = re.finditer(
        TS_SCRIPT_PARAMETERS_REGEX, params_string, re.MULTILINE | re.DOTALL
    )

    # for match_id, match in enumerate(matches, start=1):
    #     print("Match {match_id} was found at {start}-{end}: {match}".format(
    #         match_id=match_id, start=match.start(), end=match.end(), match=match.group()))
    #     for group_id in range(0, len(match.groups())):
    #         group_id = group_id + 1
    #         print("Group {group_id} found at {start}-{end}: {group}".format(group_id=group_id,
    #               start=match.start(group_id), end=match.end(group_id), group=match.group(group_id)))

    for match in matches:
        # Ignore additional match caused by adding the "," above
        if match.group(1) == "":
            continue

        # If a default value is given for the parameter, also get its type
        optional = match.group(2) is not None
        if match.group(6) is not None:
            optional = True
            default_value = code[start + match.start(6) : start + match.end(6)]
            if core.is_number(default_value):
                default_value = float(default_value)
                default_type = "number"
            elif default_value == "true" or default_value == "false":
                default_value = default_value == "true"
                default_type = "boolean"
            else:
                if default_value[0] == "'" or default_value[0] == '"':
                    default_value = default_value[1:-1]
                    default_type = "string"
        else:
            default_value = None
            default_type = "unknown"

        param_type = match.group(4) if match.group(4) is not None else default_type
        param_type_array = False
        if param_type.endswith("[]"):
            param_type = param_type[:-2]
            param_type_array = True

        param = {
            "name": match.group(1),
            "type": param_type,
            "optional": optional,
            "is_array": param_type_array,
        }

        if default_value is not None:
            param["default"] = default_value

        params.append(param)

    return params


def get_function_return_type_no_promise(code):
    result = re.findall(
        TS_SCRIPT_RESULT_REMOVE_PROMISE_REGEX, code, re.MULTILINE | re.DOTALL
    )

    return result[0] if len(result) > 0 else "void"


def get_typescript_interface_props(code, code_cleared, start, end):
    props = []
    # Add a trailing ; to allow for an easier regex
    props_string = code_cleared[start + 1 : end - 1] + ";"
    matches = re.finditer(
        TS_INTERFACE_FIELDS_REGEX, props_string, re.MULTILINE | re.DOTALL
    )

    for match in matches:
        prop = {
            "name": match.group(2) if match.group(3) is None else match.group(3),
            "type": match.group(6),
            "optional": match.group(5) is not None,
            "readOnly": match.group(1) is not None,
        }
        if match.group(4) is not None:
            prop["indexSignatureType"] = match.group(4)
        props.append(prop)

    return props


def get_mrs_typescript_interface_definitions(file, interfaces_def):

    # Find the TS interface definitions
    matches = re.finditer(
        TS_INTERFACE_REGEX, file["code_cleared"], re.MULTILINE | re.DOTALL
    )

    for match in matches:
        # Get the starting line number of the class definition
        ln_number_start = file["code"].count("\n", 0, match.start()) + 1
        ln_number_end = file["code"].count("\n", 0, match.end()) + 1
        ts_interface = {
            "file_info": {
                "full_file_name": file["full_file_name"],
                "relative_file_name": file["relative_file_name"],
                "file_name": file["file_name"],
                "last_modification": file["last_modification"],
            },
            "name": match.group(1),
            "code_position": {
                "line_number_start": ln_number_start,
                "line_number_end": ln_number_end,
                "character_start": match.start(),
                "character_end": match.end(),
            },
            "properties": get_typescript_interface_props(
                file["code"], file["code_cleared"], match.start(4), match.end(4)
            ),
        }
        if match.group(3) is not None:
            ts_interface["extends"] = match.group(3)

        interfaces_def.append(ts_interface)


def get_mrs_typescript_definitions(file, mrs_script_def):
    # Find the MRS Schema definitions, which map to TS classes
    matches = re.finditer(
        TS_SCHEMA_DECORATOR_REGEX, file["code_cleared"], re.MULTILINE | re.DOTALL
    )

    # for match_id, match in enumerate(matches, start=1):
    #     print("Match {match_id} was found at {start}-{end}: {match}".format(
    #         match_id=match_id, start=match.start(), end=match.end(), match=match.group()))
    #     for group_id in range(0, len(match.groups())):
    #         group_id = group_id + 1
    #         print("Group {group_id} found at {start}-{end}: {group}".format(group_id=group_id,
    #               start=match.start(group_id), end=match.end(group_id), group=match.group(group_id)))

    for match in matches:
        # Group 1 holds the decorator properties
        props = get_decorator_properties(
            file["code"], file["code_cleared"], match.start(2), match.end(2)
        )

        # Get the starting line number of the class definition
        ln_number_start = file["code"].count("\n", 0, match.start()) + 1
        ln_number_end = file["code"].count("\n", 0, match.end()) + 1
        schema_def = {
            "file_info": {
                "full_file_name": file["full_file_name"],
                "relative_file_name": file["relative_file_name"],
                "file_name": file["file_name"],
                "last_modification": file["last_modification"],
            },
            "class_name": file["code"][match.start(3) : match.end(3)],
            "schema_type": (
                "SCRIPT_MODULE" if match.group(1) == "module" else "DATABASE_SCHEMA"
            ),
            "code_position": {
                "line_number_start": ln_number_start,
                "line_number_end": ln_number_end,
                "character_start": match.start(),
                "character_end": match.end(),
            },
            "properties": props,
            "scripts": [],
            "triggers": [],
        }
        mrs_script_def.append(schema_def)
        class_content = match.group(4)
        class_content_offset = match.start(4)

        script_matches = re.finditer(
            TS_SCRIPT_DECORATOR_REGEX, class_content, re.MULTILINE | re.DOTALL
        )

        # for script_match_id, script_match in enumerate(script_matches, start=1):
        #     print("Match {script_match_id} was found at {start}-{end}: {match}".format(
        #         script_match_id=script_match_id, start=script_match.start(), end=script_match.end(), match=script_match.group()))
        #     for group_id in range(0, len(script_match.groups())):
        #         group_id = group_id + 1
        #         print("Group {group_id} found at {start}-{end}: {group}".format(group_id=group_id, start=script_match.start(
        #             group_id), end=script_match.end(group_id), group=script_match.group(group_id)))

        for script_match in script_matches:
            ln_number_start = (
                file["code"].count("\n", 0, class_content_offset + script_match.start())
                + 1
            )
            ln_number_end = (
                file["code"].count("\n", 0, class_content_offset + script_match.end())
                + 1
            )

            # Group 2 holds the decorator params
            props = get_decorator_properties(
                file["code"],
                file["code_cleared"],
                class_content_offset + script_match.start(2),
                class_content_offset + script_match.end(2),
            )

            params = get_function_params(
                file["code"],
                file["code_cleared"],
                class_content_offset + script_match.start(5),
                class_content_offset + script_match.end(5),
            )

            return_type = get_function_return_type_no_promise(
                file["code"][
                    class_content_offset
                    + script_match.start(6) : class_content_offset
                    + script_match.end(6)
                ]
            )
            returns_array = False
            if return_type.endswith("[]"):
                return_type = return_type[:-2]
                returns_array = True

            script_def = {
                "function_name": file["code"][
                    class_content_offset
                    + script_match.start(4) : class_content_offset
                    + script_match.end(4)
                ],
                "code_position": {
                    "line_number_start": ln_number_start,
                    "line_number_end": ln_number_end,
                    "character_start": class_content_offset + script_match.start(),
                    "character_end": class_content_offset + script_match.end(),
                },
                "parameters": params,
                "return_type": {
                    "type": return_type,
                    "is_array": returns_array,
                },
                "properties": props,
            }

            if script_match.group(1) == "trigger":
                schema_def["triggers"].append(script_def)
            else:
                schema_def["scripts"].append(script_def)


def is_simple_typescript_type(typeName):
    return typeName == "boolean" or typeName == "number" or typeName == "string"


def get_typescript_interface_from_list(type_name, interface_list):
    for interface_def in interface_list:
        if interface_def["name"] == type_name:
            return interface_def

    return None


def add_typescript_interface_to_list(type_name, interface_list, interfaces_def):
    # If the name of the type matches a simple type, do not add it
    if is_simple_typescript_type(type_name):
        return True

    # If the name is already in the list, do not add it again
    for interface_def in interface_list:
        if interface_def["name"] == type_name:
            return True

    # Look the type_name up in the list of know interface definitions
    for interface_def in interfaces_def:
        if interface_def["name"] == type_name:
            interface_list.append(interface_def)

            # If the interface extends another interface, make sure to add that interface as well, recursively
            extends = interface_def.get("extends")
            if extends is not None:  # and extends != "IMrsInterface":
                add_typescript_interface_to_list(
                    extends, interface_list, interfaces_def
                )
            return True

    return False


def match_typescript_script_types_to_interface_list(
    interfaces_def, mrs_script_def, errors
):
    used_interfaces = []

    for script_module in mrs_script_def:
        for script in script_module["scripts"]:
            # Handle return type
            return_type = script["return_type"]["type"]
            if not add_typescript_interface_to_list(
                return_type, used_interfaces, interfaces_def
            ):
                errors.append(
                    {
                        "kind": "TypeError",
                        "message": f"The script {script["function_name"]} returns an unknown datatype `{return_type}`.",
                        "script": script,
                        "file_info": script_module["file_info"],
                    }
                )

            # Handle parameters
            for parameter in script["parameters"]:
                param_type = parameter["type"]
                if param_type.endswith("[]"):
                    errors.append(
                        {
                            "kind": "TypeError",
                            "message": "A script parameter must not be an array. "
                            + f"The script {script["function_name"]} used `{parameter["type"]}` "
                            + f"as parameter type for `{parameter["name"]}`.",
                            "script": script,
                            "file_info": script_module["file_info"],
                        }
                    )
                elif not add_typescript_interface_to_list(
                    param_type, used_interfaces, interfaces_def
                ):
                    errors.append(
                        {
                            "kind": "TypeError",
                            "message": f'Unknown datatype `{param_type}` used for script parameter `{
                                parameter["name"]}`.',
                            "script": script,
                            "file_info": script_module["file_info"],
                        }
                    )

    # Use a while loop here, since the used_interfaces list can grow while the looping over the list
    i = 0
    while i < len(used_interfaces):
        interface = used_interfaces[i]
        for property in interface["properties"]:
            property_type = property["type"]
            # If the property uses an array, only match the type of the array
            if property_type.endswith("[]"):
                property_type = property_type[:-2]
            if not add_typescript_interface_to_list(
                property_type, used_interfaces, interfaces_def
            ):
                errors.append(
                    {
                        "kind": "TypeError",
                        "message": f'Unknown datatype `{property_type}` used for interface property `{
                            property["name"]}`.',
                        "interface": interface,
                        "file_info": interface["file_info"],
                    }
                )
        i += 1

    # Check that the return_type is a simple type or derives from IMrsInterface
    # for script_module in mrs_script_def:
    #     for script in script_module["scripts"]:
    #         # Handle return type
    #         return_type = script["return_type"]["type"]

    #         if not (is_simple_typescript_type(return_type) or interface_derives_from(
    #                 return_type, "IMrsInterface", used_interfaces)):
    #             errors.append({
    #                 "kind": "TypeError",
    #                 "message": f"The datatype `{return_type}` returned by the MRS script does not derive from "
    #                 + "IMrsInterface nor is a simple type.",
    #                 "script": script,
    #                 "file_info": script_module["file_info"],
    #             })

    return used_interfaces


def get_file_mrs_script_definitions(path, language):
    path = os.path.expanduser(path)

    # Read the file content
    with open(path, "r") as f:
        code = f.read()
        # Clear TypeScript comments and strings for regex matching
        code_cleared = blank_quoted_js_strings(blank_js_comments(code))
        codeFile = {
            "full_file_name": path,
            "relative_file_name": path,
            "file_name": os.path.basename(path),
            "last_modification": datetime.datetime.fromtimestamp(
                pathlib.Path(path).stat().st_mtime, tz=datetime.timezone.utc
            ).strftime("%F %T.%f")[:-3],
            "code": code,
            "code_cleared": code_cleared,
        }

    # Progress TypeScript
    mrs_script_def = []
    if language == "TypeScript":
        get_mrs_typescript_definitions(file=codeFile, mrs_script_def=mrs_script_def)

    return mrs_script_def


def get_mrs_script_definitions_from_code_file_list(
    code_files, language, send_gui_message=None
):
    # Get both, interface and script definitions
    mrs_script_modules_def = []
    interfaces_def = []
    errors = []

    if language == "TypeScript":
        for stage in ["interfaces", "scripts"]:
            for file in code_files:
                if stage == "interfaces":
                    if send_gui_message is not None:
                        send_gui_message(
                            "info",
                            f"Parsing MRS Scripts file {file["relative_file_name"]} ...",
                        )
                    get_mrs_typescript_interface_definitions(file, interfaces_def)
                elif stage == "scripts":
                    get_mrs_typescript_definitions(file, mrs_script_modules_def)

        # Limit the interface list to interfaces used in scripts and check for missing interface definitions
        used_interfaces = match_typescript_script_types_to_interface_list(
            interfaces_def, mrs_script_modules_def, errors
        )

    mrs_script_def = {
        "script_modules": mrs_script_modules_def,
        "interfaces": used_interfaces,
        "errors": errors,
        "language": language,
    }

    return mrs_script_def


def is_common_build_folder(dir):
    if (
        dir.lower() == "build"
        or dir.lower() == "output"
        or dir.lower() == "out"
        or dir.lower() == "dist"
    ):
        return True

    return False


def is_common_static_content_folder(dir):
    if (
        dir.lower() == "static"
        or dir.lower() == "assets"
        or dir.lower() == "media"
        or dir.lower() == "web"
        or dir.lower() == "js"
        or dir.lower() == "css"
        or dir.lower() == "images"
    ):
        return True

    return False


def get_code_files_from_folder(path, ignore_list, language):
    full_ignore_pattern = convert_ignore_list_to_regex_pattern(ignore_list)
    path = os.path.expanduser(path)

    code_files = []
    build_folder = None
    static_content_folders = []

    for root, dirs, files in os.walk(path):
        # Check if there is a build directory with a common name in the root dir
        if path == root:
            for dir in dirs:
                if is_common_build_folder(dir):
                    build_folder = dir
                if is_common_static_content_folder(dir):
                    static_content_folders.append(dir)

        for file in files:
            fullname = os.path.join(root, file)

            # If the filename matches the ignore list, ignore the file
            if full_ignore_pattern is not None and re.match(
                full_ignore_pattern, fullname.replace("\\", "/")
            ):
                continue

            # Progress TypeScript
            if language == "TypeScript" and (
                (fullname.endswith(".mts") or fullname.endswith(".ts"))
                and not (
                    fullname.endswith(".spec.mts")
                    or fullname.endswith(".spec.ts")
                    or fullname.endswith(".d.ts")
                )
            ):

                # Read the file content
                with open(fullname, "r") as f:
                    code = f.read()
                    # Clear TypeScript comments and strings for regex matching
                    code_cleared = blank_quoted_js_strings(blank_js_comments(code))

                    code_files.append(
                        {
                            "full_file_name": fullname,
                            "relative_file_name": fullname[len(path) :],
                            "file_name": os.path.basename(fullname),
                            "last_modification": datetime.datetime.fromtimestamp(
                                pathlib.Path(fullname).stat().st_mtime,
                                tz=datetime.timezone.utc,
                            ).strftime("%F %T.%f")[:-3],
                            "code": code,
                            "code_cleared": code_cleared,
                        }
                    )

    return code_files, build_folder, static_content_folders


def get_folder_mrs_script_definitions(
    path, ignore_list, language, send_gui_message=None
):
    code_files, build_folder, static_content_folders = get_code_files_from_folder(
        path=path, ignore_list=ignore_list, language=language
    )

    mrs_script_def = get_mrs_script_definitions_from_code_file_list(
        code_files, language, send_gui_message=send_gui_message
    )

    if build_folder is not None:
        mrs_script_def["build_folder"] = build_folder

    if len(static_content_folders) > 0:
        mrs_script_def["static_content_folders"] = static_content_folders

    if language == "TypeScript" and build_folder is None:
        mrs_script_def["errors"].append(
            {
                "kind": "BuildError",
                "message": f"No build folder found for this TypeScript project. Please build the project before adding it.",
            }
        )

    return mrs_script_def


DEFAULT_IGNORE_LIST = "*node_modules/*, */.*"


def content_file_statement(
    request_path, service_path, content_set_path, data: bytes, options: dict
):
    """Returns the CREATE REST CONTENT FILE statement for a file.

    UTF-8 text without backslashes is sent as is, everything else base64
    encoded as BINARY CONTENT, so the statement reads the same with and
    without the NO_BACKSLASH_ESCAPES SQL mode.
    """
    try:
        text = data.decode("utf-8")
        if "\0" in text or "\\" in text:
            text = None
    except UnicodeDecodeError:
        text = None

    if text is not None:
        content = "CONTENT '" + text.replace("'", "''") + "'"
    else:
        content = "BINARY CONTENT '" + base64.b64encode(data).decode("ascii") + "'"

    return (
        f"CREATE OR REPLACE REST CONTENT FILE {core.quote_ident(request_path)} ON"
        + (f" SERVICE {core.quote_ident(service_path)}" if service_path else "")
        + f" CONTENT SET {core.quote_ident(content_set_path)}\n    {content}\n"
        f"    OPTIONS {json.dumps(options)}"
    )


def load_content_set(
    session,
    directory,
    content_set_path,
    service_path=None,
    ignore_list=None,
    load_scripts=None,
    replace=False,
    send_gui_message=None,
):
    """Uploads the files of a directory to a content set via REST SQL.

    Creates the content set, sends one CREATE REST CONTENT FILE statement per
    file and registers the MRS scripts of the files with ALTER REST CONTENT
    SET ... LOAD TYPESCRIPT SCRIPTS. With load_scripts None, the scripts are
    registered if the directory holds any.

    Returns:
        A dict with the uploaded files and the result message of the last
        statement
    """
    directory = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        raise ValueError(f"The given directory '{directory}' does not exist.")
    if ignore_list is None:
        ignore_list = DEFAULT_IGNORE_LIST
    ignore_pattern = (
        convert_ignore_list_to_regex_pattern(ignore_list) if ignore_list else None
    )
    if load_scripts is None:
        load_scripts = (
            get_folder_mrs_scripts_language(directory, ignore_list) is not None
        )

    content_set_ref = core.quote_ident(content_set_path)
    on_service = f" ON SERVICE {core.quote_ident(service_path)}" if service_path else ""

    create = "CREATE OR REPLACE" if replace else "CREATE"
    res = session.run_sql(f"{create} REST CONTENT SET {content_set_ref}{on_service}")
    message = res.get_info() if hasattr(res, "get_info") else None

    files = []
    for root, dirs, file_names in os.walk(directory):
        dirs.sort()
        for file_name in sorted(file_names):
            full_name = os.path.join(root, file_name)
            request_path = "/" + os.path.relpath(full_name, directory).replace(
                "\\", "/"
            )

            # The ignore list matches the path relative to the directory
            if ignore_pattern is not None and re.match(ignore_pattern, request_path):
                continue

            with open(full_name, "rb") as f:
                data = f.read()

            if send_gui_message is not None:
                send_gui_message("info", f"Adding file {request_path} ...")

            options = {
                "last_modification": datetime.datetime.fromtimestamp(
                    pathlib.Path(full_name).stat().st_mtime, tz=datetime.timezone.utc
                ).strftime("%F %T.%f")[:-3],
            }
            session.run_sql(
                content_file_statement(
                    request_path, service_path, content_set_path, data, options
                )
            )
            files.append(request_path)

    if load_scripts:
        res = session.run_sql(
            f"ALTER REST CONTENT SET {content_set_ref}{on_service} "
            "LOAD TYPESCRIPT SCRIPTS"
        )
        message = res.get_info() if hasattr(res, "get_info") else None

    return {"files": files, "message": message}
