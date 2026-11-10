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

"""Changes applied to a schema project, and what follows every one of them.

Every lib function that writes into a project is marked @changes_project.
After it succeeds, the project's copyright years are moved along
(management.update_copyright_years), so a change of a later year shows in
the notices, whichever function made it. A call refused with an error
leaves the years as they are. Calls inside a marked call leave it to the
outermost one, so a change moves the years once.

A function whose own output carries the notices moves the years itself
before writing it, after its refusals: prepare_release and
generate_deployment_script (the release and deployment scripts), and
edit_schema (the snapshot its base_hash is checked against). The bump after
them then finds nothing left to do.

A function only ever called from a marked one needs no mark of its own.
"""

import contextvars
import functools
import inspect
from typing import Callable, Optional

# Whether a marked call is running in this context
_in_change = contextvars.ContextVar("msm_in_project_change", default=False)


def changes_project(function: Callable = None, *, writes: Callable[[dict], bool] = None):
    """Marks a lib function that writes into a schema project.

    The project is the call's `schema_project_path` argument, or the
    project the `file_path` argument lies in; a call with neither, or with a
    file outside any project, moves no years.

    Args:
        function: The function marked.
        writes: For a function that writes only on some calls: given the
            call's arguments by name, whether this one writes.
    """
    if function is None:
        return lambda function: changes_project(function, writes=writes)
    signature = inspect.signature(function)
    if not {"schema_project_path", "file_path"} & set(signature.parameters):
        raise TypeError(
            f"{function.__qualname__} is marked @changes_project but takes neither "
            "schema_project_path nor file_path."
        )

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if _in_change.get():
            return function(*args, **kwargs)
        token = _in_change.set(True)
        try:
            result = function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            if writes is None or writes(bound.arguments):
                path = _project_path(bound.arguments)
                if path is not None:
                    from msm_plugin.lib import management

                    management.update_copyright_years(path)
            return result
        finally:
            _in_change.reset(token)

    return wrapper


def _project_path(arguments: dict) -> Optional[str]:
    if arguments.get("schema_project_path"):
        return arguments["schema_project_path"]
    if arguments.get("file_path"):
        from msm_plugin.lib import management

        return management.find_schema_project_path(arguments["file_path"])
    return None
