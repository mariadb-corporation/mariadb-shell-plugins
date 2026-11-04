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

"""The MariaDB DBA Plugin, a plugin group.

The shell loads a plugin folder without an ``init.py`` as a group of
plugins one level down: ``dba_core`` creates the ``dba`` object, and each
``vendor_<name>`` adds ``dba.vendor.<name>``. This file makes the group a
regular package, so ``dba_plugin.dba_core`` and the vendors stay importable
after the shell takes the plugin directory back off ``sys.path``.
"""
