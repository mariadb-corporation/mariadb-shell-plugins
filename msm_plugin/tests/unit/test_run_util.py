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

"""run_util: a shell utility on a given session, never on another's."""

import threading
import time
from types import SimpleNamespace

import pytest

from msm_plugin.lib import core


class FakeShell:
    """The shell's global session, and every switch of it."""

    def __init__(self):
        self.session = None
        self.switches = []

    def get_session(self):
        return self.session

    def set_session(self, session):
        self.switches.append(session)
        self.session = session


class FakeUtil:
    """A utility that records what it ran on, and can take a while."""

    def __init__(self, shell, delay=0.0, fail=False, help_text=""):
        self.shell = shell
        self.delay = delay
        self.fail = fail
        self.help_text = help_text
        self.calls = []

    def help(self, name):
        return self.help_text

    def dump_schemas(self, schemas, url, options):
        on = options.get("session", self.shell.session)
        time.sleep(self.delay)
        # the session must still be the same when the work ends
        self.calls.append((schemas, url, dict(options), on,
                           options.get("session", self.shell.session)))
        if self.fail:
            raise RuntimeError("dump failed")


class ShellSession:
    """Stands in for a shell session object: its type name says shell.Object."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<shell.Object {self.name}>"


ShellSession.__name__ = ShellSession.__qualname__ = "shell.Object"


@pytest.fixture
def fake(monkeypatch):
    shell = FakeShell()
    util = FakeUtil(shell)
    monkeypatch.setattr(core, "mysqlsh",
                        SimpleNamespace(globals=SimpleNamespace(shell=shell, util=util)))
    monkeypatch.setattr(core, "_util_session_option", None)
    return SimpleNamespace(shell=shell, util=util)


def test_passes_the_session_where_the_shell_takes_it(fake):
    fake.util.help_text = "... progressCallback ..."
    session = ShellSession("a")

    core.run_util(session, "dump_schemas", ["s"], "/d", options={"threads": 2})

    assert fake.util.calls == [(["s"], "/d", {"threads": 2, "session": session}, session, session)]
    # the global session was never touched
    assert fake.shell.switches == []


def test_unwraps_a_session_wrapper(fake):
    fake.util.help_text = "progressCallback"
    session = ShellSession("a")

    core.run_util(SimpleNamespace(session=session), "dump_schemas", ["s"], "/d")

    assert fake.util.calls[0][3] is session


def test_switches_and_restores_the_global_session_on_an_older_shell(fake):
    previous = ShellSession("previous")
    fake.shell.session = previous
    session = ShellSession("a")

    core.run_util(session, "dump_schemas", ["s"], "/d", options={"threads": 2})

    assert fake.util.calls == [(["s"], "/d", {"threads": 2}, session, session)]
    assert fake.shell.switches == [session, previous]
    assert fake.shell.session is previous


def test_restores_it_also_when_the_utility_fails(fake):
    fake.util.fail = True

    with pytest.raises(RuntimeError, match="dump failed"):
        core.run_util(ShellSession("a"), "dump_schemas", ["s"], "/d")

    assert fake.shell.session is None


def test_two_calls_at_once_each_run_on_their_own_session(fake):
    fake.util.delay = 0.05
    sessions = [ShellSession(name) for name in "abcd"]
    threads = [
        threading.Thread(target=core.run_util, args=(s, "dump_schemas", [s.name], "/d"))
        for s in sessions
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # each ran on its own session from start to end
    for schemas, _, _, started_on, ended_on in fake.util.calls:
        assert started_on.name == schemas[0]
        assert ended_on.name == schemas[0]
    assert fake.shell.session is None


def test_the_lock_is_shared_by_every_plugin():
    import importlib
    import sys

    lock = core._global_session_lock()
    assert lock is core._global_session_lock()
    assert sys.modules[core._GLOBAL_SESSION_LOCK_MODULE].lock is lock
    for other in ("msm_plugin.lib.core", "mrs_plugin.lib.core"):
        try:
            module = importlib.import_module(other)
        except ImportError:
            continue
        # a plugin without the helper has no lock of its own to compare
        if hasattr(module, "_global_session_lock"):
            assert module._global_session_lock() is lock
