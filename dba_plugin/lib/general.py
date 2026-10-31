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

"""What every part of the plugin shares: its version and its error."""

# cSpell:ignore mysqlsh MariaDB

VERSION = "26.10.4"


class DbaError(Exception):
    """A refusal or failure of a dba operation, worded for the user.

    The library raises only this. The shell functions turn it into a
    ``mysqlsh.Error`` and the MCP tools into a ``ToolError``, so that each
    caller reports it the way its own surface expects.
    """


class SettingError(DbaError):
    """A deployment setting that is not valid, naming the settings it is about.

    Args:
        message (str): What is wrong, worded for the user.
        settings: The keys of the settings concerned, so that a dialog can
            point at them; empty where it is about the settings as a whole.
    """

    def __init__(self, message: str, settings=()):
        super().__init__(message)
        self.settings = [settings] if isinstance(settings, str) else list(settings or ())
