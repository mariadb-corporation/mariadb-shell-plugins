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

"""Runs a MariaDB Shell process for the MCP server and stops it with the server.

Started by :mod:`mcp_plugin.lib.util_process` as a script of its own, with the
shell's command line as its arguments. It imports nothing from the plugin, so
that ``python -I`` can run it; the plugin imports its constants and the two
signal helpers from here.

Why it exists: a dump or a load runs in a shell process of its own, and that
process has to end when the MCP server does, however the server ends. A load
left running after a window reload would otherwise go on beside the one the
extension resumes, on the same dump. The server holds this process's stdin
open; when the server is gone, for whatever reason, stdin ends here, and the
shell is interrupted as on ^C and killed if it does not stop.

The protocol on stdin:

1. The first line is JSON: ``{"input": "<text>"}``, the text written to the
   shell's stdin - the passwords it is asked for, one per line.
2. Every further line is a command. ``cancel`` interrupts the shell as ^C
   does, ``kill`` ends it at once. An end of file interrupts it too, and kills
   it if it has not ended :data:`GRACE_SECONDS` later.

The shell writes to this process's stdout and stderr directly. Once it has
ended, one more line is written to stdout: ``{"mcpShellExit": {"returncode":
<n>}}``, where ``n`` is negative for a signal, as :mod:`subprocess` reports
it, so that the server can tell a crash from a failure.
"""

import json
import os
import signal
import subprocess
import sys
import threading

EXIT_MARKER = "mcpShellExit"
GRACE_SECONDS = 30


def interrupt_shell(process) -> None:
    """Interrupts a shell process as ^C does."""
    try:
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    except OSError:
        pass


def kill_shell(process) -> None:
    """Ends a shell process at once."""
    try:
        process.kill()
    except OSError:
        pass


def _follow_commands(process) -> None:
    """Reads the commands on stdin until it ends, then stops the shell."""
    for line in sys.stdin:
        command = line.strip()
        if command == "cancel":
            interrupt_shell(process)
        elif command == "kill":
            kill_shell(process)

    # the server is gone, or done with the shell
    if process.poll() is None:
        interrupt_shell(process)
        try:
            process.wait(GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            kill_shell(process)


def main() -> int:
    """Runs the shell given on the command line."""
    first = sys.stdin.readline()
    try:
        text = json.loads(first).get("input", "") if first.strip() else ""
    except ValueError:
        text = ""

    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        sys.argv[1:], stdin=subprocess.PIPE, creationflags=creation_flags
    )
    try:
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
    except OSError:
        pass

    threading.Thread(target=_follow_commands, args=(process,), daemon=True).start()

    returncode = process.wait()
    sys.stdout.write(json.dumps({EXIT_MARKER: {"returncode": returncode}}) + "\n")
    sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
