# Copyright (c) 2026, Oracle and/or its affiliates.
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

from pathlib import Path

import pytest
import gui_plugin.debugger.Debugger as debugger_module


def _set_debugger_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(debugger_module, "__file__", str(root / "Debugger.py"))


def test_read_script_reads_file_inside_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    script = scripts_dir / "allowed.js"
    script.write_text("print('ok')", encoding="utf-8")

    _set_debugger_root(monkeypatch, tmp_path)

    assert debugger_module.read_script("allowed.js") == "print('ok')"


def test_read_script_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    _set_debugger_root(monkeypatch, tmp_path)

    with pytest.raises(Exception) as exc:
        debugger_module.read_script("../secret.txt")

    assert str(exc.value) == "The requested story does not exist: ../secret.txt"


def test_read_script_rejects_absolute_path_outside_scripts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()

    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    _set_debugger_root(monkeypatch, tmp_path)

    with pytest.raises(Exception) as exc:
        debugger_module.read_script(str(outside.resolve()))

    assert str(exc.value) == f"The requested story does not exist: {outside.resolve()}"
