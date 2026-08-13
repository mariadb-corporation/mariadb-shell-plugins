# Copyright (c) 2026, MariaDB plc.
#
# SPDX-License-Identifier: GPL-2.0-only
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

"""Coverage bootstrap for subprocesses spawned during the test run.

The MCP tools execute in a separate ``mariadb-shell`` stdio subprocess, not in
the pytest process, so their coverage would otherwise be missed. The test
runner puts this directory on the subprocess PYTHONPATH and sets
COVERAGE_PROCESS_START for it; ``coverage.process_startup()`` then starts
coverage in the subprocess. It is a harmless no-op when COVERAGE_PROCESS_START
is not set (e.g. in the pytest process itself).
"""

try:
    import coverage

    coverage.process_startup()
except Exception:
    pass
