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

"""The dba plugin's information functions."""

# cSpell:ignore mysqlsh MariaDB

from mysqlsh.plugin_manager import plugin_function

from dba_plugin.lib.general import VERSION


@plugin_function("dba.info", shell=True, cli=True, web=True)
def info() -> str:
    """Returns basic information about this plugin.

    Returns:
        str
    """
    return (
        f"MariaDB DBA Plugin Version {VERSION} PREVIEW\n"
        "Warning! For testing purposes only!"
    )


@plugin_function("dba.version", shell=True, cli=True, web=True)
def version() -> str:
    """Returns the version number of the plugin.

    Returns:
        str
    """
    return VERSION
