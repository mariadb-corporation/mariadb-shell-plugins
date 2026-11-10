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

"""A project made from a schema on a server (lib/live_schema.py)."""

import os
from pathlib import Path

import pytest

from msm_plugin.lib import live_schema, lsp, modeling

LIVE_SCHEMA = "msm_live_import_test"
needs_lsp = pytest.mark.skipif(lsp.find_lsp() is None, reason="no mariadb-lsp binary")

_SCHEMA_SQL = [
    "CREATE SEQUENCE `order_numbers` START WITH 1000",
    "CREATE TABLE `orders` (`id` INT AUTO_INCREMENT PRIMARY KEY, `customer_id` INT,"
    " `note_id` INT, `number` INT DEFAULT NEXTVAL(`order_numbers`),"
    " CONSTRAINT `orders_customer` FOREIGN KEY (`customer_id`) REFERENCES `customer` (`id`),"
    " CONSTRAINT `orders_note` FOREIGN KEY (`note_id`) REFERENCES `note` (`id`))"
    " COMMENT 'Orders'",
    "CREATE TABLE `customer` (`id` INT AUTO_INCREMENT PRIMARY KEY, `name` VARCHAR(40))",
    "CREATE TABLE `note` (`id` INT PRIMARY KEY, `text` TEXT)",
    "INSERT INTO `customer` (`name`) VALUES ('a'), ('b')",
    f"CREATE VIEW `customer_names` AS SELECT `name` FROM `{LIVE_SCHEMA}`.`customer`",
    "CREATE VIEW `a_first_names` AS SELECT `name` FROM `customer_names`",
    "CREATE PROCEDURE `add_customer`(IN p VARCHAR(40)) INSERT INTO `customer` (`name`) VALUES (p)",
    "CREATE FUNCTION `twice`(x INT) RETURNS INT DETERMINISTIC RETURN x * 2",
    "CREATE TRIGGER `orders_bi` BEFORE INSERT ON `orders` FOR EACH ROW SET NEW.`note_id` = NULL",
    "CREATE EVENT `cleanup` ON SCHEDULE EVERY 1 DAY DO DELETE FROM `note`",
    "CREATE VIEW `msm_schema_version` (`major`, `minor`, `patch`) AS SELECT 1, 2, 3",
]


@pytest.fixture
def live(sandbox_session):
    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{LIVE_SCHEMA}`")
    sandbox_session.run_sql(f"CREATE SCHEMA `{LIVE_SCHEMA}`")
    sandbox_session.run_sql(f"USE `{LIVE_SCHEMA}`")
    sandbox_session.run_sql("SET FOREIGN_KEY_CHECKS = 0")
    for sql in _SCHEMA_SQL:
        sandbox_session.run_sql(sql)
    sandbox_session.run_sql("SET FOREIGN_KEY_CHECKS = 1")
    yield sandbox_session
    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{LIVE_SCHEMA}`")


def names(items):
    return [item["name"] for item in items]


def test_the_objects_of_a_live_schema_are_listed(live):
    listed = live_schema.list_schema_objects(live, LIVE_SCHEMA)

    assert names(listed["tables"]) == ["customer", "note", "orders"]
    assert listed["tables"][2] == {"name": "orders", "comment": "Orders", "triggers": ["orders_bi"]}
    assert names(listed["views"]) == ["a_first_names", "customer_names"]
    assert names(listed["procedures"]) == ["add_customer"]
    assert names(listed["functions"]) == ["twice"]
    assert listed["triggers"] == [{"name": "orders_bi", "table": "orders"}]
    assert names(listed["events"]) == ["cleanup"]
    assert names(listed["sequences"]) == ["order_numbers"]
    # msm's own objects are never imported; the version it deployed is told
    assert listed["left_out"] == ["msm_schema_version"]
    assert listed["schema_version"] == "1.2.3"

    with pytest.raises(ValueError, match="has no schema `nowhere`"):
        live_schema.list_schema_objects(live, "nowhere")


@pytest.mark.parametrize("objects, message", [
    ({"tables": ["nothing"]}, "has no table `nothing`"),
    ({"triggers": ["orders_bi"]}, "needs its table `orders`"),
    ({"indexes": []}, "Unknown object kind `indexes`"),
    ({"views": "customer_names"}, "must be a list of names"),
    (["orders"], "must be an object"),
])
def test_a_bad_selection_is_refused_before_anything_is_written(live, tmp_path, objects, message):
    with pytest.raises(ValueError, match=message):
        modeling.create_project_from_live_schema(live, LIVE_SCHEMA, str(tmp_path), objects=objects)
    assert list(tmp_path.iterdir()) == []


@needs_lsp
def test_a_live_schema_becomes_a_project(live, tmp_path):
    result = modeling.create_project_from_live_schema(
        live, LIVE_SCHEMA, str(tmp_path), copyright_holder="ACME Corp."
    )

    assert (result["tables"], result["sequences"], result["views"], result["routines"],
            result["triggers"], result["events"]) == (3, 1, 2, 2, 1, 1)
    assert result["schema_version"] == "1.2.3"
    assert result["warnings"] == []
    project = result["project_path"]
    script = "".join(
        Path(path).read_text() for path in result["files"] if path.endswith(".sql")
    )
    # Nothing ties the statements to the server they were read from
    assert "DEFINER=" not in script
    # (the template's CREATE SCHEMA and USE name it, as in every project)
    assert f"`{LIVE_SCHEMA}`.`customer`" not in script
    assert "AUTO_INCREMENT=" not in script
    assert "msm_schema_version` AS SELECT 1" not in script
    # Each table after the tables it refers to, the sequence first; each
    # view after the view it selects from
    assert script.index("CREATE SEQUENCE `order_numbers`") < script.index("CREATE TABLE `customer`") \
        < script.index("CREATE TABLE `orders`")
    assert script.index("VIEW `customer_names`") < script.index("VIEW `a_first_names`")
    assert "DROP PROCEDURE IF EXISTS `add_customer`%%" in script
    assert "DROP TRIGGER IF EXISTS `orders_bi`%%" in script
    assert "CREATE OR REPLACE EVENT `cleanup`" in script

    snapshot = modeling.read_diagram_rows(project)[0]["document"]
    assert sorted(names(snapshot["tables"])) == ["customer", "note", "orders"]
    # (msm_schema_version: the project's own, of Section 120)
    assert sorted(names(snapshot["views"])) == ["a_first_names", "customer_names", "msm_schema_version"]


@needs_lsp
def test_a_selection_of_a_live_schema_becomes_a_project(live, tmp_path):
    result = modeling.create_project_from_live_schema(
        live, LIVE_SCHEMA, str(tmp_path), copyright_holder="ACME Corp.",
        objects={"tables": ["orders", "customer"], "sequences": ["order_numbers"],
                 "functions": ["twice"]},
    )

    assert (result["tables"], result["sequences"], result["views"], result["routines"],
            result["triggers"], result["events"]) == (2, 1, 0, 1, 1, 0)
    # The foreign key to a table left out goes with a warning
    assert result["warnings"] == [
        "The foreign key `orders_note` of `orders` refers to `note`, which is not imported; "
        "it was left out."
    ]
    script = "".join(
        Path(path).read_text() for path in result["files"] if path.endswith(".sql")
    )
    # Its index stays, as the server made it for the foreign key
    assert "CONSTRAINT `orders_customer`" in script and "CONSTRAINT `orders_note`" not in script
    assert "CREATE TABLE `note`" not in script
    # The table brings its trigger
    assert "DROP TRIGGER IF EXISTS `orders_bi`%%" in script
    assert os.path.isdir(result["project_path"])


def test_a_foreign_key_left_out_keeps_the_statement_valid():
    sql = (
        "CREATE TABLE `orders` (\n"
        "  `id` int(11) NOT NULL AUTO_INCREMENT,\n"
        "  `note_id` int(11) DEFAULT NULL,\n"
        "  PRIMARY KEY (`id`),\n"
        "  CONSTRAINT `orders_note` FOREIGN KEY (`note_id`) REFERENCES `note` (`id`)\n"
        ") ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4"
    )
    table, left_out = live_schema.table_sql(sql, "shop", {"orders"})
    assert left_out == [("orders_note", "note")]
    assert table == (
        "CREATE TABLE `orders` (\n"
        "  `id` int(11) NOT NULL AUTO_INCREMENT,\n"
        "  `note_id` int(11) DEFAULT NULL,\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    # One to another schema stays: the project depends on it
    other = sql.replace("REFERENCES `note`", "REFERENCES `crm`.`note`")
    assert live_schema.table_sql(other, "shop", {"orders"})[1] == []


@needs_lsp
def test_the_imported_project_deploys_the_schema_again(live, tmp_path):
    from msm_plugin import lib as msm_lib

    result = modeling.create_project_from_live_schema(
        live, LIVE_SCHEMA, str(tmp_path), copyright_holder="ACME Corp."
    )
    before = live_schema.list_schema_objects(live, LIVE_SCHEMA)
    live.run_sql(f"DROP SCHEMA `{LIVE_SCHEMA}`")

    dev_file = msm_lib.management.get_schema_development_file_path(result["project_path"])
    msm_lib.core.execute_msm_sql_script(
        live, sql_file_path=dev_file, take_lock=False,
        substitutions=msm_lib.management.resolve_substitutions(
            msm_lib.management.get_project_settings(result["project_path"])
        ),
    )
    after = live_schema.list_schema_objects(live, LIVE_SCHEMA)
    for kind in live_schema.OBJECT_KINDS:
        assert after[kind] == before[kind], kind


def test_without_a_trigger_list_the_tables_bring_their_triggers(live):
    listed = live_schema.list_schema_objects(live, LIVE_SCHEMA)
    assert listed["project_folder_name"] == f"{LIVE_SCHEMA}.msm.project"
    assert live_schema.selected_objects(listed, {"tables": ["orders"]})["triggers"] == ["orders_bi"]
    assert live_schema.selected_objects(listed, {"tables": ["customer"]})["triggers"] == []
    # An explicit list is taken as given
    assert live_schema.selected_objects(
        listed, {"tables": ["orders"], "triggers": []}
    )["triggers"] == []


class _Task:
    """What the import reports to, as the shell's task manager's tasks take it."""

    def __init__(self, cancel_at_stage=None):
        self.events = []
        self.cancel_at_stage = cancel_at_stage

    def check_cancelled(self):
        if self.cancel_at_stage and self.cancel_at_stage in [e[1] for e in self.events]:
            raise RuntimeError("The task was cancelled.")

    def stage_started(self, stage):
        self.events.append(("started", stage))

    def stage_finished(self, stage, seconds=None):
        self.events.append(("finished", stage))

    def progress(self, current, total, stage=None):
        self.events.append(("progress", stage, current, total))


@needs_lsp
def test_the_import_reports_its_stages_to_a_task(live, tmp_path):
    task = _Task()
    modeling.create_project_from_live_schema(
        live, LIVE_SCHEMA, str(tmp_path), copyright_holder="ACME Corp.", task=task
    )
    stages = [event for event in task.events if event[0] != "progress"]
    assert stages == [
        ("started", "Reading the objects"), ("finished", "Reading the objects"),
        ("started", "Writing the project"), ("finished", "Writing the project"),
        ("started", "Making the schema snapshot"), ("finished", "Making the schema snapshot"),
    ]
    steps = [event for event in task.events if event[0] == "progress"]
    # One step per object: 3 tables, 1 sequence, 2 views, 2 routines, 1 trigger, 1 event
    assert steps[-1] == ("progress", "Reading the objects", 10, 10)


@needs_lsp
def test_a_cancelled_import_leaves_no_project(live, tmp_path):
    with pytest.raises(RuntimeError, match="cancelled"):
        modeling.create_project_from_live_schema(
            live, LIVE_SCHEMA, str(tmp_path), copyright_holder="ACME Corp.",
            task=_Task(cancel_at_stage="Making the schema snapshot"),
        )
    assert list(tmp_path.iterdir()) == []


def test_the_new_project_is_checked_before_the_objects_are_read(live, tmp_path):
    with pytest.raises(ValueError, match="as this path does not exist"):
        modeling.create_project_from_live_schema(
            live, LIVE_SCHEMA, str(tmp_path / "missing"), copyright_holder="ACME Corp.",
            task=_Task(),
        )


def test_the_schemas_own_name_goes_only_where_it_qualifies_a_name():
    from msm_plugin.lib import statements

    sql = ("select `shop`.`t`.`id` AS `id`, 'in `shop`.`t` a string' from `shop` . `t`"
           " -- `shop`.`c` in a comment\n join `it's`.`shop` on 1")
    assert statements.unqualified(sql, "shop") == (
        "select `t`.`id` AS `id`, 'in `shop`.`t` a string' from `t`"
        " -- `shop`.`c` in a comment\n join `it's`.`shop` on 1"
    )
    assert statements.without_definer(
        "CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`localhost` SQL SECURITY DEFINER VIEW `v` AS select 1"
    ) == "CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `v` AS select 1"
