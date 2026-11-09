# Copyright (c) 2026, MariaDB plc.
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

import os
from types import SimpleNamespace

import pytest

from msm_plugin.lib import online_alter, statements, update_steps


# -----------------------------------------------------------------------------
# Reading a step's body


def test_top_level_statements_keep_compound_statements_whole():
    body = """
    -- a comment; with a semicolon
    ALTER TABLE `t` ADD COLUMN `n` INT;
    UPDATE `t` SET `n` = IF(`v` > 0, 1, 2), `w` = CASE WHEN `v` THEN 'END;' ELSE 'x' END;
    IF (SELECT COUNT(*) FROM `u`) = 0 THEN
        INSERT INTO `u` VALUES (1);
        IF 1 THEN DO 1; ELSEIF 2 THEN DO 2; ELSE BEGIN DO 3; END; END IF;
    END IF;
    lbl: LOOP
        LEAVE lbl;
    END LOOP lbl;
    WHILE 0 DO DO 1; END WHILE;
    REPEAT DO 1; UNTIL 1 END REPEAT;
    CASE 1 WHEN 1 THEN DO 1; ELSE DO 2; END CASE;
    DROP TABLE IF EXISTS `x`;
    CREATE TABLE IF NOT EXISTS `y` (`a` INT);
    SELECT REPEAT('a', 3) `END`;
    /* trailing */
    """
    parts = statements.top_level_statements(body)
    assert [statements.first_line(statements.strip_statement(part)) for part in parts] == [
        "ALTER TABLE `t` ADD COLUMN `n` INT",
        "UPDATE `t` SET `n` = IF(`v` > 0, 1, 2), `w` = CASE WHEN `v` THEN 'END;' ELSE 'x' END",
        "IF (SELECT COUNT(*) FROM `u`) = 0 THEN",
        "lbl: LOOP",
        "WHILE 0 DO DO 1; END WHILE",
        "REPEAT DO 1; UNTIL 1 END REPEAT",
        "CASE 1 WHEN 1 THEN DO 1; ELSE DO 2; END CASE",
        "DROP TABLE IF EXISTS `x`",
        "CREATE TABLE IF NOT EXISTS `y` (`a` INT)",
        "SELECT REPEAT('a', 3) `END`",
    ]
    assert parts[2].endswith("END IF")


@pytest.mark.parametrize("body", ["IF 1 THEN DO 1;", "DO 1; END;", "BEGIN DO 1; END IF; END LOOP;"])
def test_unbalanced_compound_statements_are_an_error(body):
    with pytest.raises(ValueError):
        statements.top_level_statements(body)


@pytest.mark.parametrize("sql, parts", [
    ("ALTER TABLE `t` ADD COLUMN `n` INT;", (None, "t", "ADD COLUMN `n` INT", [])),
    ("-- msm.editSchema: add_column t n\nalter table s.t add n int",
     ("s", "t", "add n int", [])),
    ("ALTER TABLE `my.s` . `t``x` DROP `a`", ("my.s", "t`x", "DROP `a`", [])),
    ("ALTER ONLINE IGNORE TABLE IF EXISTS t ADD UNIQUE (a)",
     (None, "t", "ADD UNIQUE (a)", ["ONLINE", "IGNORE", "IF EXISTS"])),
])
def test_alter_table_parts(sql, parts):
    found = statements.alter_table_parts(sql)
    assert (found.schema, found.table, found.spec, found.modifiers) == parts


@pytest.mark.parametrize("sql", ["ALTER VIEW v AS SELECT 1", "UPDATE t SET a = 1",
                                 "ALTER EVENT e RENAME TO f"])
def test_other_statements_are_no_alter_table(sql):
    assert statements.alter_table_parts(sql) is None


def test_a_steps_alters_stand_alone_and_the_rest_runs_in_blocks():
    actions = update_steps.step_actions(
        "    UPDATE t SET a = 1;\n    IF 1 THEN DO 1; END IF;\n"
        "    ALTER TABLE t ADD b INT;\n    ALTER TABLE t ADD c INT;\n    DELETE FROM t;\n")
    assert [action.kind for action in actions] == ["block", "alter", "alter", "block"]
    assert actions[0].sql == ("BEGIN NOT ATOMIC\nUPDATE t SET a = 1\n;\n"
                              "IF 1 THEN DO 1; END IF\n;\nEND")
    assert actions[1].sql == "ALTER TABLE t ADD b INT"
    assert update_steps.step_actions("    -- nothing here\n") == []


@pytest.mark.parametrize("body, note", [
    ("DECLARE c INT;\nSELECT COUNT(*) INTO c FROM t;\nALTER TABLE t ADD b INT;",
     "it declares variables or handlers"),
    ("ALTER TABLE t ADD b INT;\nEND IF;", "its compound statements could not be read"),
])
def test_a_step_that_cannot_be_split_runs_whole(body, note):
    actions = update_steps.step_actions(body)
    assert len(actions) == 1 and actions[0].kind == "block"
    assert actions[0].note.startswith(note)


def test_read_steps_and_the_call():
    script = (
        "BEGIN\n    -- ### MSM-UPDATE-STEP-START 1.0.0 1.0.1\n    ALTER TABLE t ADD a INT;\n"
        "    -- ### MSM-UPDATE-STEP-END 1.0.0 1.0.1\nEND%%\n"
        "BEGIN\n    -- ### MSM-UPDATE-STEP-START 1.0.1 1.1.0\n"
        "    -- ### MSM-UPDATE-STEP-END 1.0.1 1.1.0\nEND%%\n"
    )
    steps = update_steps.read_steps(script)
    assert [(s.version_from, s.version_to) for s in steps] == [("1.0.0", "1.0.1"), ("1.0.1", "1.1.0")]
    assert steps[0].body.strip() == "ALTER TABLE t ADD a INT;"
    assert update_steps.is_create_or_update_call("CALL `msm_create_or_update`();")
    assert not update_steps.is_create_or_update_call("CALL `msm_create_1.0.0`()")


# -----------------------------------------------------------------------------
# Deployments on the sandbox

SCHEMA = "msm_update_steps_test"

TABLES_1_0_0 = (
    "CREATE TABLE `t` (`id` INT PRIMARY KEY, `v` INT, `w` INT);\n"
    "CREATE TABLE `u` (`id` INT PRIMARY KEY);"
)
# Each ALTER goes another way: INSTANT, NOCOPY, COPY; DML and a compound
# statement between them
UPDATE_1_0_1 = """\
ALTER TABLE `t` ADD COLUMN `n` INT;
UPDATE `t` SET `n` = `v` * 2;
IF (SELECT COUNT(*) FROM `u`) = 0 THEN
    INSERT INTO `u` VALUES (1);
END IF;
ALTER TABLE `t` ADD INDEX `ix_n` (`n`);
ALTER TABLE `t` MODIFY `w` VARCHAR(20);"""
# A DECLARE: the step runs whole, its ALTER as written
UPDATE_1_0_2 = """\
DECLARE c INT DEFAULT 0;
SELECT COUNT(*) INTO c FROM `t`;
ALTER TABLE `t` ADD COLUMN `m` INT DEFAULT 7;"""


@pytest.fixture(scope="module")
def project(tmp_path_factory, sandbox_session):
    """A project released as 1.0.0, 1.0.1 and 1.0.2, deployment scripts
    generated."""
    from msm_plugin.management import (create_new_project_folder, generate_deployment_script,
                                       prepare_release, set_section_sql_content)

    path = create_new_project_folder(schema_name=SCHEMA,
                                     target_path=str(tmp_path_factory.mktemp("steps")),
                                     copyright_holder="Example Ltd.")
    dev_file = os.path.join(path, "development", f"{SCHEMA}_next.sql")
    set_section_sql_content(file_path=dev_file, section_id="140", sql_content=TABLES_1_0_0)
    prepare_release(schema_project_path=path, version="1.0.0", next_version="1.0.1")
    generate_deployment_script(schema_project_path=path, version="1.0.0")
    for version, next_version, update in (("1.0.1", "1.0.2", UPDATE_1_0_1),
                                          ("1.0.2", "1.0.3", UPDATE_1_0_2)):
        files = prepare_release(schema_project_path=path, version=version,
                                next_version=next_version)
        update_file = next(f for f in files if os.sep + "updates" + os.sep in f)
        set_section_sql_content(file_path=update_file, section_id="240", sql_content=update)
        generate_deployment_script(schema_project_path=path, version=version)
    return path


def deploy(session, path, version, **kwargs):
    from msm_plugin.lib import management

    return management.deploy_schema(session=session, schema_project_path=path,
                                    version=version, **kwargs)


def fresh_1_0_0(session, path):
    session.run_sql(f"DROP SCHEMA IF EXISTS `{SCHEMA}`")
    deploy(session, path, "1.0.0")
    session.run_sql(f"INSERT INTO `{SCHEMA}`.`t` VALUES (1, 10, 100), (2, 20, 200)")


def state(session) -> dict:
    def rows(sql):
        return [tuple(row) for row in session.run_sql(sql).fetch_all()]

    return {
        "t": rows(f"SHOW CREATE TABLE `{SCHEMA}`.`t`"),
        "u": rows(f"SHOW CREATE TABLE `{SCHEMA}`.`u`"),
        "t rows": rows(f"SELECT * FROM `{SCHEMA}`.`t` ORDER BY `id`"),
        "u rows": rows(f"SELECT * FROM `{SCHEMA}`.`u` ORDER BY `id`"),
        "version": rows(f"SELECT * FROM `{SCHEMA}`.`msm_schema_version`"),
        "routines": rows("SELECT ROUTINE_NAME FROM information_schema.ROUTINES "
                         f"WHERE ROUTINE_SCHEMA = '{SCHEMA}'"),
    }


class Log:
    """What the update log gets from here on."""

    def __init__(self):
        from msm_plugin import lib

        self.path = lib.core.get_msm_schema_update_log_path()
        self.start = os.path.getsize(self.path) if os.path.exists(self.path) else 0

    def text(self) -> str:
        with open(self.path) as f:
            f.seek(self.start)
            return f.read()


@pytest.fixture
def clean(sandbox_session):
    yield
    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{SCHEMA}`")


def test_an_update_through_msm_ends_as_one_through_the_procedure(sandbox_session, project, clean):
    fresh_1_0_0(sandbox_session, project)
    log = Log()
    deploy(sandbox_session, project, "1.0.2")
    through_msm = state(sandbox_session)
    text = log.text()
    assert "ALTER TABLE `t`: INSTANT" in text
    assert "ALTER TABLE `t`: NOCOPY" in text
    assert "ALTER TABLE `t`: COPY" in text
    assert "The update to 1.0.2 runs as one block: it declares variables or handlers" in text

    fresh_1_0_0(sandbox_session, project)
    log = Log()
    deploy(sandbox_session, project, "1.0.2", online_alter=False)
    assert "ALTER TABLE `t`:" not in log.text()
    assert state(sandbox_session) == through_msm
    assert through_msm["version"] == [(1, 0, 2)]
    assert through_msm["u rows"] == [(1,)]
    assert through_msm["t rows"] == [(1, 10, "100", 20, 7), (2, 20, "200", 40, 7)]
    assert through_msm["routines"] == []


def test_a_table_turned_off_runs_as_written(sandbox_session, project, clean):
    fresh_1_0_0(sandbox_session, project)
    log = Log()
    deploy(sandbox_session, project, "1.0.1", online_alter={"tables": {"t": {"enabled": False}}})
    assert log.text().count("ALTER TABLE `t` runs as written (online ALTER TABLE is off") == 3
    assert state(sandbox_session)["version"] == [(1, 0, 1)]


def test_an_alter_no_online_step_applies_stops_the_deployment(sandbox_session, project, clean):
    fresh_1_0_0(sandbox_session, project)
    with pytest.raises(update_steps.NeedsExternalToolError,
                       match=r"from 1.0.0 to 1.0.1 needs an external tool for an ALTER TABLE "
                             r"of `t`: online rebuilds \(INPLACE, COPY\) skipped: .*; "
                             r"blocking writes skipped"):
        deploy(sandbox_session, project, "1.0.1",
               online_alter={"maxRebuildBytes": 0, "maxLockedBytes": 0})
    now = state(sandbox_session)
    # The statements before it ran; it did not; the update is marked as running
    assert "`n` int(11)" in now["t"][0][1] and "KEY `ix_n`" in now["t"][0][1]
    assert "`w` int(11)" in now["t"][0][1]
    assert now["version"] == [(0, 0, 0)]


def test_a_script_without_marked_steps_runs_as_written(sandbox_session, project, clean, tmp_path):
    import shutil

    copy = str(tmp_path / "old")
    shutil.copytree(project, copy)
    script_path = os.path.join(copy, "releases", "deployment", f"{SCHEMA}_deployment_1.0.1.sql")
    with open(script_path) as f:
        script = f.read()
    with open(script_path, "w") as f:
        f.write("".join(line for line in script.splitlines(True)
                        if "MSM-UPDATE-STEP-" not in line))
    fresh_1_0_0(sandbox_session, copy)
    log = Log()
    deploy(sandbox_session, copy, "1.0.1")
    assert "marks no update steps" in log.text()
    assert "ALTER TABLE `t`:" not in log.text()
    assert state(sandbox_session)["version"] == [(1, 0, 1)]


def test_another_schemas_table_gets_the_general_values_only(monkeypatch):
    settings = online_alter.DeploymentSettings(
        online_alter.read_setting({"lockWait": 7, "tables": {"t": {"enabled": False}}}, "test"))
    used, as_written = [], []

    def fake_alter(session, schema, table, spec, options):
        used.append((schema, options.lock_wait))
        return online_alter.Result(online_alter.INSTANT, None, 0, False)

    monkeypatch.setattr(online_alter, "alter_table", fake_alter)
    step = update_steps.UpdateStep("1.0.0", "1.0.1", "")
    session = SimpleNamespace(run_sql=as_written.append)
    for sql in ("ALTER TABLE other.t ADD c INT", "ALTER TABLE `shop`.t ADD c INT",
                "ALTER TABLE t ADD c INT"):
        update_steps._alter(session, step, sql, statements.alter_table_parts(sql), "shop", settings,
                            lambda level, message: None)
    # The project's t is off; shop.t and t are it, other.t is not
    assert used == [("other", 7)]
    assert as_written == ["ALTER TABLE `shop`.t ADD c INT", "ALTER TABLE t ADD c INT"]
