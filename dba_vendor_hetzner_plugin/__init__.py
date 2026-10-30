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

"""The Hetzner Cloud vendor plugin of the MariaDB DBA Plugin.

Deliberately empty: ``init.py`` registers ``dba.vendor.hetzner`` and may run
only once, and :func:`dba_plugin.lib.vendors.discover` imports the
``vendor`` module directly. See :mod:`dba_plugin.lib.objects`.
"""
