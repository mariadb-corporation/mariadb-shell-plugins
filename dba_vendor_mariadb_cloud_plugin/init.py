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

It registers the MariaDB Cloud vendor and its dba.vendor.mariadbCloud shell
object, with the functions every dba vendor has.
"""

# cSpell:ignore mysqlsh MariaDB

from dba_plugin.lib import objects

from dba_vendor_mariadb_cloud_plugin.vendor import MariadbCloud

objects.register_vendor_plugin(MariadbCloud)
