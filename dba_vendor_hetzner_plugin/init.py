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

It registers the Hetzner Cloud vendor and its dba.vendor.hetzner shell
object, whose functions dba_plugin.lib.objects makes for every vendor alike.
"""

# cSpell:ignore mysqlsh MariaDB

from dba_plugin.lib import objects

from dba_vendor_hetzner_plugin.vendor import Hetzner

objects.register_vendor_plugin(Hetzner)
