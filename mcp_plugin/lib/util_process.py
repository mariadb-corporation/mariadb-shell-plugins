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

"""Runs a shell utility in a MariaDB Shell process of its own.

A dump, load, copy, export or import runs as ``mariadb-shell --json=raw
--passwords-from-stdin --uri=<uri> -- util <command> ...``, not in the MCP
server's own process: a utility that crashes takes only its own process down,
and the server reports it, and resumes a load that can be resumed.

- **The command line** (:func:`build_command`) follows the shell's command
  line mapper (``mysqlshdk/shellcore/shell_cli_mapper.cc``). A list argument
  takes every unnamed argument after it, so what follows one is named:
  ``--outputUrl``, or a copy's target as its connection attributes. Every
  value is typed, so that the mapper does not guess: ``--name:str=``,
  ``--name:json=`` for lists and dictionaries, plain for numbers and booleans.
- **Passwords** never reach the command line: the shell asks for them, and
  they are written to its stdin.
- **Its output** is one JSON object per line. :class:`OutputParser` turns
  them into the events :meth:`mcp_plugin.lib.tasks.Task.on_event` records:
  messages, stages and their progress.
- **The process** runs under :mod:`mcp_plugin.lib.util_watchdog`, which stops
  it when the server goes away, however the server ends.
"""
# cSpell:ignore mysqlsh MariaDB nocolor libproc pidpath thds

import atexit
import collections
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import weakref
from typing import Optional

from mcp_plugin.lib.tool_registrar import tool_error

# The line the watchdog writes once the shell has ended; see util_watchdog.
EXIT_MARKER = "mcpShellExit"

# How long a cancelled shell gets to stop before it is killed.
CANCEL_GRACE_SECONDS = 30

# How often a load whose shell process crashed is started again.
MAX_RESTARTS = 3

# The first stage a load starts once it has read its progress file, and reset
# it where the resetProgress option asks for that (Dump_loader::execute_tasks).
_PROGRESS_FILE_READ = "Scanning metadata"

# The lines of the shell's stderr kept for the report of a crash.
STDERR_LINES = 20

_WATCHDOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "util_watchdog.py")

# The utilities, by the name of their tool: the command, and how each of their
# arguments is given on the command line - "value" unnamed, "list" as an
# unnamed JSON list, "outputUrl" named, "target" as connection attributes.
_COMMANDS = {
    "dump_instance": ("dump-instance", ("value",)),
    "dump_schemas": ("dump-schemas", ("list", "outputUrl")),
    "dump_tables": ("dump-tables", ("value", "list", "outputUrl")),
    "export_table": ("export-table", ("value", "value")),
    "load_dump": ("load-dump", ("value",)),
    "import_table": ("import-table", ("list",)),
    "copy_instance": ("copy-instance", ("target",)),
    "copy_schemas": ("copy-schemas", ("list", "target")),
    "copy_tables": ("copy-tables", ("value", "list", "target")),
}

# Names that are both an option of the copy utilities and a connection
# attribute of their target: the mapper takes them only with the name of the
# parameter in front (`util copy-schemas --help` lists them).
_COPY_AMBIGUOUS = ("schema",)

# What the mapper unescapes in a string it is given: a string holding one of
# these would reach the utility changed.
_ESCAPES = ("\\,", '\\"', "\\'", "\\=")

_STAGE_PREFIX = re.compile(r"^((?:SRC|TGT): )")
_STAGE_DONE = " - done"


# --- the shell -----------------------------------------------------------------


def _own_executable() -> Optional[str]:
    """The executable of this process: the shell the server runs in."""
    try:
        if sys.platform.startswith("linux"):
            return os.path.realpath("/proc/self/exe")

        import ctypes

        if sys.platform == "darwin":
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            buffer = ctypes.create_string_buffer(4096)
            if libproc.proc_pidpath(os.getpid(), buffer, ctypes.sizeof(buffer)) > 0:
                return buffer.value.decode("utf-8")
        elif os.name == "nt":
            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer)):
                return buffer.value
    except Exception:  # noqa: BLE001 - then the shell is looked for on PATH
        pass

    return None


def shell_executable() -> str:
    """The MariaDB Shell binary a utility runs in.

    The one the server itself runs in, so that the utility is that version's;
    the one on PATH where this process is not a shell (a plain Python).

    Returns:
        str: Its path.
    """
    own = _own_executable()
    if own and os.path.isfile(own) and not os.path.basename(own).lower().startswith("python"):
        return own

    found = shutil.which("mariadb-shell")
    if found:
        return found

    raise tool_error(
        "The MariaDB Shell binary to run the utility in was not found. Put "
        "mariadb-shell on the PATH."
    )


def _watchdog_python() -> Optional[str]:
    """The Python to run the watchdog with: the shell's own, if it has one."""
    executable = sys.executable or ""
    if os.path.basename(executable).lower().startswith("python") and os.path.isfile(executable):
        return executable

    return None


# --- the command line ----------------------------------------------------------


def _check_string(value: str, what: str) -> None:
    """Refuses a string the command line cannot pass on unchanged."""
    for escape in _ESCAPES:
        if escape in value:
            raise tool_error(
                f"{what} contains '{escape}', which the shell's command line "
                "cannot pass on unchanged."
            )


def _check_strings(value, what: str) -> None:
    """Refuses a list or dictionary holding such a string."""
    if isinstance(value, str):
        _check_string(value, what)
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_string(str(key), what)
            _check_strings(item, what)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_strings(item, what)


def _unnamed_value(value, what: str) -> str:
    """An unnamed string argument, refused where the mapper would misread it.

    The mapper parses an unnamed argument as JSON and keeps it a string only
    where that gives a string, or a number written differently, so "2024",
    "true" or "[1]" would reach the utility as something else; and one that
    begins with "-" is taken for an option.
    """
    if not isinstance(value, str) or not value:
        raise tool_error(f"{what} must be a non-empty string.")

    _check_string(value, what)

    misread = value.startswith("-")
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = value
    if isinstance(parsed, (bool, list, dict)) or parsed is None:
        misread = True
    elif isinstance(parsed, (int, float)) and str(parsed) == value:
        misread = True

    if misread:
        raise tool_error(
            f"{what} '{value}' cannot be passed on the shell's command line, "
            "which would read it as an option or a value of another type."
        )

    return value


def _named(name: str, value) -> Optional[str]:
    """A named argument, typed so that the mapper does not guess."""
    if value is None:
        return None

    if isinstance(value, bool):
        return f"--{name}={'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"--{name}={value!r}"
    if isinstance(value, str):
        _check_string(value, f"The option '{name}'")
        return f"--{name}:str={value}"

    _check_strings(value, f"The option '{name}'")
    return f"--{name}:json={json.dumps(value)}"


def _target_arguments(target: dict) -> list:
    """A copy's target as connection attributes, without its password."""
    arguments = []
    for key, value in target.items():
        if key == "password" or value is None:
            continue
        name = f"connection-data-{key}" if key in _COPY_AMBIGUOUS else key
        if key == "port":
            arguments.append(f"--{name}={int(value)}")
        else:
            arguments.append(_named(name, value))

    return arguments


def build_command(function: str, args: list, options: dict, target: Optional[dict] = None) -> list:
    """The arguments of ``-- util ...`` for a utility.

    Args:
        function (str): The utility, as its tool names it ("dump_schemas").
        args (list): Its arguments before the target and the options.
        options (dict): Its options, with their camelCase names.
        target (dict): A copy's target, as connection data.

    Returns:
        list: The arguments, beginning with "util".
    """
    command, kinds = _COMMANDS[function]
    arguments = ["util", command]
    values = list(args)

    for kind in kinds:
        if kind == "target":
            arguments += _target_arguments(target or {})
            continue

        value = values.pop(0)
        if kind == "list":
            items = [value] if isinstance(value, str) else list(value)
            _check_strings(items, "A name")
            arguments.append(json.dumps(items))
        elif kind == "outputUrl":
            if not isinstance(value, str) or not value:
                raise tool_error("The output URL must be a non-empty string.")
            arguments.append(_named("outputUrl", value))
        else:
            arguments.append(_unnamed_value(value, "The argument"))

    copy = command.startswith("copy-")
    for name, value in options.items():
        if copy and name in _COPY_AMBIGUOUS:
            name = f"options-{name}"
        argument = _named(name, value)
        if argument:
            arguments.append(argument)

    # without a terminal the shell would show no progress at all
    arguments.append("--showProgress=true")

    return arguments


# --- the output ----------------------------------------------------------------


def _data_stage(kind: str, line: dict) -> str:
    """The name of the stage that moves the data; its lines carry none."""
    if "loadProgress" in line:
        return "Loading data"
    if "dumpProgress" in line:
        return "Exporting data" if kind == "export_table" else "Dumping data"
    if kind == "import_table":
        return "Importing data"

    return "Loading data" if kind == "load_dump" else "Dumping data"


class OutputParser:
    """Turns the shell's JSON lines into the events of a task.

    The events are those :meth:`mcp_plugin.lib.tasks.Task.on_event` takes:
    "message", "stageStarted", "progress" and "stageFinished". What the shell
    prints:

    - A message: ``{"<level>": "<text>\\n"}``, the level one of info, status,
      note, warning and error.
    - A stage without a count: ``{"info": "<stage>"}`` when it starts and
      ``{"info": "<stage> - done"}`` when it ends, with no line break.
    - A stage that counts: ``numericProgressUpdate`` with its description.
    - The stage that moves the data: ``throughputProgressUpdate``, which has
      no name (see :func:`_data_stage`), and ``throughputProgressConfig`` with
      what an item is.
    - A copy puts "SRC: " or "TGT: " in front of the text, and its loader
      prints its stages as messages: "<stage>..." and "<stage> - done".
    - An error ends the run: the last error line is the error of the task, and
      is not repeated as a message.
    """

    def __init__(self, kind: str):
        self.kind = kind
        self.exit_code = None
        self.error = None
        self._started = {}
        self._items = {}
        self._pending_error = None

    def feed(self, text: str) -> list:
        """The events of one line of the shell's output."""
        try:
            line = json.loads(text)
        except ValueError:
            line = None

        if isinstance(line, dict) and EXIT_MARKER in line:
            self.exit_code = line[EXIT_MARKER].get("returncode")
            return []

        events = []
        if self._pending_error is not None:
            # it was not the last line: a message after all
            events.append(_message("error", self._pending_error))
            self._pending_error = None

        if not isinstance(line, dict):
            if text.strip():
                events.append(_message("info", text.rstrip("\n")))
            return events

        if "prompt" in line:
            return events

        level, message = next(
            ((key, line[key]) for key in ("error", "warning", "note", "status", "info")
             if isinstance(line.get(key), str)),
            (None, None),
        )
        prefix_match = _STAGE_PREFIX.match(message or "")
        prefix = prefix_match.group(1) if prefix_match else ""

        numeric = line.get("numericProgressUpdate")
        throughput = line.get("throughputProgressUpdate")
        config = line.get("throughputProgressConfig")

        if numeric:
            stage = prefix + str(numeric.get("description", ""))
            events += self._start(stage)
            events.append({
                "type": "progress", "stage": stage,
                "current": numeric.get("current", 0), "total": numeric.get("total", 0),
                "totalKnown": numeric.get("totalKnown", True),
            })
            if (message or "").rstrip().endswith(_STAGE_DONE):
                events += self._finish(stage)
            return events

        if throughput or config:
            stage = prefix + _data_stage(self.kind, line)
            events += self._start(stage)
            if config:
                self._items[stage] = config
            if throughput:
                config = self._items.get(stage, {})
                rate = throughput.get("throughput") or {}
                seconds = rate.get("seconds") or 1
                event = {
                    "type": "progress", "stage": stage,
                    "current": throughput.get("current", 0),
                    "total": throughput.get("total", 0),
                    "throughput": round(rate.get("items", 0) / seconds, 2),
                    "etaSeconds": throughput.get("etaSeconds"),
                }
                if "itemsFull" in config:
                    event["items"] = config["itemsFull"]
                if "totalIsApproximate" in config:
                    event["totalIsApproximate"] = config["totalIsApproximate"]
                # an unknown total ("?%") is no percentage
                event["totalKnown"] = bool(event["total"])
                events.append(event)
            return events

        if message is None:
            return events

        if level == "error":
            if "exceptionInfo" not in line:
                self._pending_error = message.strip()
            self.error = message.strip()
            return events

        if not message.endswith("\n"):
            # a stage without a count
            body = message.strip()
            if body.endswith(_STAGE_DONE):
                return events + self._finish(body[: -len(_STAGE_DONE)])
            return events + self._start(body)

        body = message.rstrip("\n")
        if not body.strip():
            return events
        events.append(_message(level, body))

        # a copy's loader prints its stages as messages
        if body.endswith("...") and "\n" not in body:
            events += self._start(body[:-3])
        elif body.endswith(_STAGE_DONE) and "\n" not in body:
            events += self._finish(body[: -len(_STAGE_DONE)])

        return events

    def _start(self, stage: str) -> list:
        if not stage or stage in self._started:
            return []
        self._started[stage] = time.monotonic()
        return [{"type": "stageStarted", "stage": stage}]

    def _finish(self, stage: str) -> list:
        started = self._started.pop(stage, None)
        if started is None:
            return []
        return [{
            "type": "stageFinished", "stage": stage,
            "seconds": round(time.monotonic() - started, 1),
        }]

    def finish_all(self) -> list:
        """Ends every stage still running: the run completed."""
        events = []
        for stage in list(self._started):
            events += self._finish(stage)
        return events


def _message(level: str, text: str) -> dict:
    return {"type": "message", "level": level, "text": text}


# --- the process ---------------------------------------------------------------

_running = weakref.WeakSet()


class ShellProcess:
    """A shell process running a utility, under the watchdog where it can.

    Args:
        arguments (list): The shell's arguments.
        input_text (str): What to write to its stdin: the passwords.
    """

    def __init__(self, arguments: list, input_text: str):
        python = _watchdog_python()
        environment = dict(os.environ, MARIADB_SHELL_TERM_COLOR_MODE="nocolor")
        # its own process group: a ^C in the server's terminal is not the
        # utility's, which is stopped through the watchdog
        group = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True}
        )

        self.watched = python is not None
        command = ([python, "-I", _WATCHDOG] if self.watched else []) + arguments
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=environment, **group,
        )
        self.stderr = collections.deque(maxlen=STDERR_LINES)
        self._lock = threading.Lock()

        if self.watched:
            self._write(json.dumps({"input": input_text}) + "\n")
        else:
            self._write(input_text)
            self._close_stdin()

        threading.Thread(target=self._read_stderr, daemon=True).start()
        _running.add(self)

    def _write(self, text: str) -> None:
        with self._lock:
            try:
                self.process.stdin.write(text.encode("utf-8"))
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass

    def _close_stdin(self) -> None:
        with self._lock:
            try:
                self.process.stdin.close()
            except OSError:
                pass

    def _read_stderr(self) -> None:
        for raw in self.process.stderr:
            text = raw.decode("utf-8", "replace").rstrip()
            # the client library's charset notes are no news
            if text and not text.startswith("Warning: Charset id"):
                self.stderr.append(text)

    def lines(self):
        """The lines the shell writes to stdout, until it ends."""
        for raw in self.process.stdout:
            yield raw.decode("utf-8", "replace")

    def cancel(self) -> None:
        """Interrupts the utility as ^C does, and kills it if it goes on."""
        if self.watched:
            self._write("cancel\n")
        else:
            try:
                self.process.send_signal(
                    signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
                )
            except OSError:
                pass

        timer = threading.Timer(CANCEL_GRACE_SECONDS, self.kill)
        timer.daemon = True
        timer.start()

    def kill(self) -> None:
        """Ends the shell at once."""
        if self.process.poll() is not None:
            return

        if self.watched:
            # the watchdog ends with its shell; killed itself, it would leave
            # the shell running
            self._write("kill\n")
            self._close_stdin()
        else:
            try:
                self.process.kill()
            except OSError:
                pass

    def stop(self) -> None:
        """Interrupts the shell, and has it killed if it does not stop.

        What the server does when it ends: closing the watchdog's stdin is
        what its end does in any case.
        """
        if self.watched:
            self._close_stdin()
        else:
            self.kill()

    def wait(self) -> int:
        """Waits for the end of the process; its return code."""
        returncode = self.process.wait()
        self._close_stdin()
        _running.discard(self)
        return returncode


@atexit.register
def _stop_all() -> None:
    """Stops the utilities still running when the server ends."""
    for process in list(_running):
        process.stop()


def _signal_name(returncode: int) -> str:
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"signal {-returncode}"


def _crash_description(returncode) -> str:
    if returncode is None:
        return "the process ended unexpectedly"
    if returncode < 0:
        return f"it was ended by {_signal_name(returncode)}"
    return f"it ended with exit code {returncode} without reporting an error"


def _resumable(function: str, args: list, options: dict) -> bool:
    """Whether a run that crashed can be started again where it stopped.

    Only a load can: it records what it loaded in its progress file, which
    is kept with the dump unless the dump is a URL of a pre-authenticated
    request, or the option switches it off.
    """
    if function != "load_dump":
        return False

    progress_file = options.get("progressFile")
    if progress_file == "":
        return False
    if progress_file is None:
        url = str(args[0]) if args else ""
        return not url.lower().startswith(("http://", "https://"))

    return True


def run(task, function: str, args: list, options: dict, source: tuple, target: Optional[dict] = None):
    """Runs a utility for a task, in shell processes of its own.

    The events of its output go to the task as they come. A run that crashed
    is started again where that resumes it (a load), at most
    :data:`MAX_RESTARTS` times; otherwise it fails with what happened.

    Args:
        task (Task): The task, which follows the run.
        function (str): The utility, as its tool names it.
        args (list): Its arguments before the target and the options.
        options (dict): Its options.
        source (tuple): ``(uri, password)`` of the connection it runs on.
        target (dict): A copy's target, as connection data with its password.

    Returns:
        None, once the utility completed.

    Raises:
        RuntimeError: With the utility's error, or what ended the process.
    """
    uri, password = source
    resumable = _resumable(function, args, options)
    attempts = MAX_RESTARTS + 1 if resumable else 1
    progress_file_read = False

    for attempt in range(1, attempts + 1):
        run_options = dict(options)
        if progress_file_read:
            # what was loaded before the crash stays loaded; until a run got
            # that far, the progress file may still be another load's
            run_options.pop("resetProgress", None)

        arguments = (
            [shell_executable(), "--json=raw", "--passwords-from-stdin", f"--uri={uri}", "--"]
            + build_command(function, args, run_options, target)
        )
        passwords = [password or ""]
        if target is not None:
            passwords.append(target.get("password") or "")

        if task.cancel_requested:
            raise RuntimeError("Interrupted by user")

        process = ShellProcess(arguments, "".join(p + "\n" for p in passwords))
        parser = OutputParser(task.kind)
        task.set_canceller(process.cancel)
        try:
            for line in process.lines():
                for event in parser.feed(line):
                    if event.get("stage") == _PROGRESS_FILE_READ:
                        progress_file_read = True
                    task.on_event(event)
            returncode = process.wait()
        finally:
            task.set_canceller(None)
            process.kill()

        if parser.exit_code is not None:
            returncode = parser.exit_code
        elif process.watched:
            # the watchdog itself was ended before it could tell
            returncode = None

        if returncode == 0:
            for event in parser.finish_all():
                task.on_event(event)
            return None

        if task.cancel_requested:
            raise RuntimeError(parser.error or "Interrupted by user")

        if parser.error is not None and returncode is not None and returncode > 0:
            raise RuntimeError(parser.error)

        # a crash
        crash = (
            f"The MariaDB Shell process running the utility crashed: "
            f"{_crash_description(returncode)}."
        )
        details = list(process.stderr)
        task.fail_running_stages()
        for text in details:
            task.on_event(_message("error", text))

        if attempt < attempts:
            task.on_event(_message(
                "warning",
                f"{crash} Resuming the load, attempt {attempt + 1} of {attempts}.",
            ))
            continue

        if resumable:
            crash += f" It was started {attempts} times."
        raise RuntimeError(crash)

    return None

