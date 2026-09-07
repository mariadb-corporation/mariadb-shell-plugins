# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Interactive prompt primitives shared by the mcp.setup flows.

Both :mod:`mcp_plugin.lib.setup` and :mod:`mcp_plugin.lib.setup_migrator` ask
the user questions, so the primitives live here rather than in either of them:
`setup` imports `setup_migrator` to build its menu, so anything `setup_migrator`
needed back from `setup` would be an import cycle.

Everything goes through :func:`shell`, which is looked up per call rather than
held in a module global - that is the single seam the tests replace to script a
whole setup run's answers.
"""

# cSpell:ignore mysqlsh MariaDB

import mysqlsh


def shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


# The labels a `confirm` prompt answers with. The shell returns the LABEL of the
# chosen answer, ampersand included (the & marks the letter that acts as the
# shortcut), so these are compared against rather than assumed.
YES_LABEL = "&Yes"
NO_LABEL = "&No"

# The answer appended to a `select` prompt to let the user back out of it. The
# shell's select prompt has no cancel of its own: it re-asks until it gets a
# valid index, so without an explicit option there is no way out.
CANCEL_LABEL = "Cancel"


def ask(message: str, options: dict = None) -> str:
    """Prompts the user for input, returning the entered (stripped) string."""
    return shell().prompt(message, options if options is not None else {}).strip()


def password(message: str) -> str:
    """Prompts the user for a password without echoing it."""
    return shell().prompt(message, {"type": "password"})


def yes_no(message: str, default: bool = True) -> bool:
    """Prompts the user for a yes/no answer.

    A `confirm` prompt, so the shell renders the answers, applies the default on
    an empty reply and re-asks until the reply is one it recognizes - none of
    which is reimplemented here.

    Args:
        message (str): The question to ask.
        default (bool): The answer to use when the user just presses Enter.

    Returns:
        The user's answer as a boolean.
    """
    answer = shell().prompt(
        message,
        {
            "type": "confirm",
            "defaultValue": YES_LABEL if default else NO_LABEL,
        },
    )

    return answer == YES_LABEL


def select(message: str, choices: list, default: int = None) -> int:
    """Prompts the user to pick one of the given choices.

    A `select` prompt, so the shell prints the numbered list, validates the
    reply and re-asks until it gets a valid one. It answers with the TEXT of the
    chosen entry, which is turned back into a position here.

    Args:
        message (str): The prompt message.
        choices (list): The choices to offer, in order. They have to be
            distinct: the answer is the text, so duplicates could not be told
            apart.
        default (int): The zero-based choice to apply on an empty reply. None
            (the default) means the prompt re-asks until an answer is given.

    Returns:
        The selected zero-based index.
    """
    options = {"type": "select", "options": list(choices)}
    if default is not None:
        # The shell's defaultValue for a select is the 1-based index.
        options["defaultValue"] = default + 1

    return list(choices).index(shell().prompt(message, options))


def select_or_cancel(message: str, choices: list) -> int:
    """Prompts the user to pick one of the given choices, or to back out.

    :data:`CANCEL_LABEL` is offered as a further choice, since the shell's
    select prompt has no cancel of its own.

    Args:
        message (str): The prompt message.
        choices (list): The choices to offer, in order.

    Returns:
        The selected zero-based index, or -1 if the user cancelled.
    """
    index = select(message, list(choices) + [CANCEL_LABEL])

    return -1 if index == len(choices) else index
