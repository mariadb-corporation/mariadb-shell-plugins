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

"""Plugin registration

This file is automatically loaded by the MariaDB Shell at startup time.

It registers the plugin objects and then imports all sub-modules to
register the plugin object member functions. It must run once only, which
is why the package's __init__.py does not import it: see
dba_plugin.lib.objects.
"""

from mysqlsh.plugin_manager import plugin

from dba_plugin.lib import objects

# cSpell:ignore mysqlsh MariaDB

# A vendor plugin may have been loaded first and created dba and dba.vendor
# already; registering an existing object again is a no-op.


@plugin
class dba:
    __doc__ = objects.DBA_DOC

    class list:
        """Lists vendors and their objects."""

        class vendor:
            """Lists what a vendor supports and has deployed."""

    class get:
        """Gets information about vendor objects."""

        class vendor:
            """Gets what a vendor offers and the status of its objects."""

    class set:
        """Sets the dba plugin's configuration."""

    class validate:
        """Validates what vendor objects are created with."""

        class vendor:
            """Validates the settings of an object at a vendor."""

    class create:
        """Creates vendor objects."""

        class vendor:
            """Creates an object at a vendor."""

    class update:
        """Updates vendor objects."""

        class vendor:
            """Updates an object at a vendor."""

    class delete:
        """Deletes vendor objects."""

        class vendor:
            """Deletes an object at a vendor."""

    class vendor:
        __doc__ = objects.VENDOR_DOC

    def __init__(self):
        """Constructor that will import all relevant sub-modules

        The constructor is called by the @plugin decorator to
        automatically register all decorated functions in the sub-modules
        """
        # Import all sub-modules to register the decorated functions there
        from dba_plugin import functions, general
