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

"""The MariaDB DBA Plugin.

Deliberately empty: ``init.py`` registers the plugin's shell objects and
functions, and it may run only once. A vendor plugin imports
``dba_plugin.lib`` to attach itself to ``dba.vendor``, and that import must
not run ``init.py`` a second time - the shell refuses to register a function
twice, and the vendor plugin would then fail to load.
"""
