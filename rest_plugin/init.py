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

"""Plugin registration

This file is automatically loaded by the MariaDB Shell at startup time.

It registers the plugin objects and then imports all sub-modules to
register the plugin object member functions.
"""

from mysqlsh.plugin_manager import plugin

# cSpell:ignore mrs, mysqlsh, InnoDB

# Create a class representing the structure of the plugin and use the
# @plugin decorator to register it


@plugin
class rest:
    """Global object for the MariaDB REST Service (MRS).

    The MariaDB REST Service is managed with REST SQL statements, e.g.
    CONFIGURE REST METADATA, CREATE REST SERVICE or SHOW REST SERVICES,
    which MariaDB Shell runs in SQL mode and through session.run_sql() on a
    MariaDB server. A file of REST SQL statements is run like any SQL
    script, e.g. with \\source in SQL mode or with --sql -f <file>.

    This object adds what REST SQL does not cover: the generation of the
    MRS SDK files and the dumping and loading of MRS services, content sets
    and projects.
    """

    def __init__(self):
        """Constructor that will import all relevant sub-modules

        The constructor is called by the @plugin decorator to
        automatically register all decorated functions in the sub-modules
        """
        # Import all sub-modules to register the decorated functions there
        from rest_plugin import general, services, content_sets, dump

    class get:
        """Used to get MRS SDK code and MRS script definitions.

        A collection of functions to get MRS SDK code and MRS script
        definitions
        """

    class dump:
        """Used to dump MRS SDK files, services, projects and the audit log.

        A collection of functions to dump MRS SDK files, REST services, MRS
        projects and the audit log
        """

    class load:
        """Used to load REST services, content sets and MRS projects.

        A collection of functions to load REST services, upload directories
        to REST content sets and load MRS projects
        """
