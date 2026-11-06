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

"""Tests for splitting sections by object kind: lib/sections.py and lib/statements.py."""

import os

import pytest

from msm_plugin.tests.unit import helpers
from msm_plugin.lib import lsp, management, sections, source, statements
from msm_plugin.lib.management import create_schema_project_folder

LSP = lsp.find_lsp()
needs_lsp = pytest.mark.skipif(LSP is None, reason="no mariadb-lsp binary")

SECTION_140 = """SET @msm_split_note = 'stays before the includes';

-- -----------------------------------------------------------------------------
-- TABLE `customer`
-- -----------------------------------------------------------------------------

CREATE TABLE `customer` (`id` INT PRIMARY KEY, `name` VARCHAR(100));

INSERT INTO `customer` VALUES (1, ' Ann '); -- the first customer

CREATE TABLE `orders` (`id` INT PRIMARY KEY, `customer_id` INT);

/* The foreign key, once both tables exist */
ALTER TABLE `orders` ADD CONSTRAINT `fk_orders_customer`
    FOREIGN KEY (`customer_id`) REFERENCES `customer` (`id`);

CREATE INDEX `ix_customer_name` ON `customer` (`name`);"""

SECTION_150 = """DELIMITER %%

/*
-- ToDo: Add schema objects create statements
CREATE OR REPLACE VIEW `my_view` AS SELECT 1%%
*/

-- -----------------------------------------------------------------------------
-- VIEW `customer_names`, on a FUNCTION created after it
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW `customer_names` AS
    SELECT `shout`(`name`) AS `name` FROM `customer`%%

DROP FUNCTION IF EXISTS `shout`%%
CREATE FUNCTION `shout`(`s` VARCHAR(100)) RETURNS VARCHAR(100) DETERMINISTIC
BEGIN
    RETURN UPPER(TRIM(s));
END%%

DROP TRIGGER IF EXISTS `customer_bi`%%
CREATE TRIGGER `customer_bi` BEFORE INSERT ON `customer` FOR EACH ROW
BEGIN
    SET NEW.`name` = TRIM(NEW.`name`);
END%%

CREATE OR REPLACE EVENT `msm_split_nightly` ON SCHEDULE EVERY 1 DAY DISABLE
    DO DELETE FROM `orders` WHERE `id` < 0%%

DELIMITER ;"""

SECTION_170 = """CREATE ROLE IF NOT EXISTS `shop_reader`;
GRANT SELECT ON `customer_names` TO `shop_reader`;"""


def make_project(tmp_path, schema_name="shop") -> str:
    path = create_schema_project_folder(
        schema_name=schema_name, target_path=str(tmp_path),
        copyright_holder="Example Ltd.", license="MIT",
    )
    dev_file = management.get_schema_development_file_path(path)
    for section_id, sql in (("140", SECTION_140), ("150", SECTION_150), ("170", SECTION_170)):
        management.set_section_sql_content(section_id, "\n" + sql + "\n", dev_file)
    return path


def dev_script(path) -> str:
    with open(management.get_schema_development_file_path(path)) as f:
        return f.read()


def body(path, section_id) -> str:
    return management.get_script_sections(dev_script(path))[section_id]["sql_content"].strip()


def breakout(path, file_name) -> str:
    with open(os.path.join(path, "development", "sections", file_name)) as f:
        return f.read()


def expanded_statements(path) -> list:
    """The statements the script runs, includes expanded, as the server gets them."""
    dev_file = management.get_schema_development_file_path(path)
    script, _ = source.expand_source_directives(dev_script(path), os.path.dirname(dev_file))
    return [
        statements.strip_statement(script.encode("utf-8")[e["begin"]:e["end"]].decode("utf-8"))
        for e in lsp.split(LSP, script) if e["kind"] == "sql"
    ]


# -----------------------------------------------------------------------------
# Statements


def test_statements_are_classified_by_what_they_are_about():
    kind = statements.object_kind
    assert kind("CREATE TABLE t (i INT);") == "TABLE"
    assert kind("-- c\nCREATE OR REPLACE SEQUENCE s;") == "TABLE"
    assert kind("ALTER TABLE `t` ADD CONSTRAINT `fk` FOREIGN KEY (a, b) REFERENCES u (a, b);") == (
        "TABLE_ADDITION")
    assert kind("ALTER TABLE s.t ADD INDEX i (a), -- c, d\n  ADD UNIQUE KEY u (b);") == (
        "TABLE_ADDITION")
    assert kind("ALTER TABLE t ADD PRIMARY KEY (a), ADD CHECK (a > 0);") == "TABLE_ADDITION"
    # Anything else would change what the section does once it runs elsewhere
    assert kind("ALTER TABLE t ADD c INT;") is None
    assert kind("ALTER TABLE t ADD INDEX i (a), DROP COLUMN b;") is None
    assert kind("ALTER TABLE a RENAME TO b;") is None
    assert kind("ALTER SEQUENCE s RESTART 1;") is None
    assert kind("CREATE UNIQUE INDEX i ON t (c);") == "TABLE_ADDITION"
    assert kind("REPLACE INTO t VALUES (1);") == "INSERT"
    assert kind("DROP PROCEDURE IF EXISTS p%%") == "PROCEDURE"
    assert kind("CREATE DEFINER=`a`@`%` FUNCTION f() RETURNS INT RETURN 1%%") == "FUNCTION"
    assert kind("CREATE OR REPLACE SQL SECURITY INVOKER VIEW v AS SELECT 1") == "VIEW"
    assert kind("SET @x = 1;") is None
    assert kind("/*!50001 CREATE VIEW v AS SELECT 1 */;") is None


def test_byte_offsets_become_character_offsets():
    text = "-- ä\nSELECT 'ü';"
    assert statements.char_offsets(text, [0, 6, 7, 19, 6]) == [0, 5, 6, 16, 5]


@needs_lsp
def test_a_section_is_cut_without_losing_text():
    script = "-- head\n" + SECTION_150 + "\n"
    cut = statements.cut_section(
        script, 8, len(script), statements.sql_entries(script, lsp.split(LSP, script)))
    assert cut.preamble.startswith("/*\n-- ToDo") and cut.preamble.endswith("*/")
    assert cut.trailer == ""
    assert [s.kind for s in cut.statements] == [
        "VIEW", "FUNCTION", "FUNCTION", "TRIGGER", "TRIGGER", "EVENT"
    ]
    assert all(s.delimiter == "%%" for s in cut.statements)
    assert cut.statements[0].text.startswith("-- ----")
    assert cut.statements[0].text.endswith("FROM `customer`%%")
    assert "DELIMITER" not in "".join(s.text for s in cut.statements)


def test_statements_are_written_back_in_delimiter_blocks():
    s = statements.Statement
    sql = statements.render([
        s("SET @a = 1;", ";", None), s("CREATE VIEW v AS SELECT 1%%", "%%", "VIEW"),
        s("CREATE VIEW w AS SELECT 2%%", "%%", "VIEW"), s("SET @b = 2;", ";", None),
    ])
    assert sql == (
        "SET @a = 1;\n\nDELIMITER %%\n\nCREATE VIEW v AS SELECT 1%%\n\n"
        "CREATE VIEW w AS SELECT 2%%\n\nDELIMITER ;\n\nSET @b = 2;"
    )


# -----------------------------------------------------------------------------
# Splitting


def test_the_template_examples_go_once_a_section_has_a_statement(tmp_path):
    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="X", license="MIT")
    script = helpers_read(management.get_schema_development_file_path(path))
    assert script.count("-- ToDo:") == 2
    # Empty sections keep them
    assert sections.without_template_examples(script) is script

    with_table = script.replace(
        "-- #############################################################################\n\n/*\n-- ToDo: Add schema TABLEs",
        "-- #############################################################################\n\n"
        "CREATE TABLE `t` (`id` INT);\n\n/*\n-- ToDo: Add schema TABLEs", 1)
    cleaned = sections.without_template_examples(with_table)
    tables = management.get_script_sections(cleaned)["140"]["sql_content"]
    assert tables.strip() == "CREATE TABLE `t` (`id` INT);"
    assert "CREATE TABLE `my_table`" not in cleaned
    # Section 150 has no statement yet: its example stays
    assert cleaned.count("-- ToDo:") == 1 and "my_view" in cleaned
    # Every other section is as it was
    before, after = (management.get_script_sections(text) for text in (with_table, cleaned))
    assert [k for k in before if before[k]["full_content"] != after[k]["full_content"]] == ["140"]

    # A SOURCE line counts as a statement; the empty DELIMITER pair goes too
    with_source = script.replace(
        "DELIMITER %%\n\n/*\n-- ToDo: Add schema objects",
        "SOURCE './sections/150-20_views.sql';\n\nDELIMITER %%\n\n/*\n-- ToDo: Add schema objects", 1)
    objects = management.get_script_sections(
        sections.without_template_examples(with_source))["150"]["sql_content"]
    assert objects.strip() == "SOURCE './sections/150-20_views.sql';"


def helpers_read(file_path):
    with open(file_path) as f:
        return f.read()


@needs_lsp
def test_sections_are_split_by_object_kind(tmp_path):
    path = make_project(tmp_path)
    before = expanded_statements(path)

    result = sections.split_sections(path)
    assert result.split == {
        "140": ["140-10_tables.sql", "140-20_table_additions.sql", "140-30_inserts.sql"],
        "150": ["150-10_procedures_functions.sql", "150-20_views.sql",
                "150-30_triggers.sql", "150-40_events.sql"],
        "170": ["170_roles.sql"],
    }
    assert result.skipped == {} and result.sections_added == []

    # The statements that leave a section keep their comments
    assert "-- TABLE `customer`" in breakout(path, "140-10_tables.sql")
    assert "VALUES (1, ' Ann '); -- the first customer" in breakout(path, "140-30_inserts.sql")
    assert "/* The foreign key, once both tables exist */\nALTER TABLE" in breakout(
        path, "140-20_table_additions.sql")
    assert breakout(path, "150-10_procedures_functions.sql").count("DELIMITER %%") == 1
    # The rest stays inline, the routines are included before the views
    assert body(path, "140").startswith("SET @msm_split_note")
    # The template's examples went with the first statements of the sections
    assert "ToDo" not in body(path, "140") and "ToDo" not in body(path, "150")
    included = [os.path.basename(file) for _, file in source.directives(body(path, "150"))]
    assert included == [
        "150-10_procedures_functions.sql", "150-20_views.sql",
        "150-30_triggers.sql", "150-40_events.sql",
    ]
    assert "DELIMITER" not in body(path, "150")

    # The script runs the same statements, only in another order
    after = expanded_statements(path)
    assert sorted(after) == sorted(before)
    assert after.index(next(s for s in after if s.startswith("CREATE FUNCTION"))) < after.index(
        next(s for s in after if s.startswith("CREATE OR REPLACE VIEW `customer_names`")))

    # Splitting again changes nothing
    again = sections.split_sections(path)
    assert again.split == {} and again.files == []
    assert set(again.skipped) == {"140", "150", "170"}


@needs_lsp
def test_one_section_can_be_split(tmp_path):
    path = make_project(tmp_path)
    result = sections.split_sections(path, ["170"])
    assert list(result.split) == ["170"]
    assert body(path, "170").startswith("SOURCE './sections/170_roles.sql'")
    assert "CREATE TABLE" in body(path, "140")
    with pytest.raises(ValueError, match="cannot be split"):
        sections.split_sections(path, ["160"])


@needs_lsp
def test_sections_that_cannot_be_split_are_left_as_they_are(tmp_path):
    path = make_project(tmp_path)
    dev_file = management.get_schema_development_file_path(path)
    # A statement no file takes between two that would move
    management.set_section_sql_content(
        "140", "\nCREATE TABLE a (i INT);\nSET @x = 1;\nCREATE TABLE b (i INT);\n", dev_file)
    # A breakout file that exists with other content
    os.makedirs(os.path.join(path, "development", "sections"))
    with open(os.path.join(path, "development", "sections", "170_roles.sql"), "w") as f:
        f.write("-- mine\n")
    original = dev_script(path)

    result = sections.split_sections(path, ["140", "170"])
    assert result.split == {} and result.files == []
    assert result.skipped["140"].endswith("statements that would move: SET @x = 1")
    assert "170_roles.sql" in result.skipped["170"]
    assert dev_script(path) == original
    assert breakout(path, "170_roles.sql") == "-- mine\n"


@needs_lsp
def test_a_new_project_has_nothing_to_split(tmp_path):
    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.",
        license="MIT",
    )
    original = dev_script(path)
    result = sections.split_sections(path)
    assert result.skipped == {section_id: "It has no statement to move."
                              for section_id in ("140", "150", "170")}
    assert dev_script(path) == original


@needs_lsp
def test_a_current_snapshot_stays_current(tmp_path):
    from msm_plugin.lib import modeling

    path = make_project(tmp_path)
    modeling.create_diagram(path, "Main", views=["customer_names"], routines=["shout"])
    assert modeling.list_diagrams(path)["schema_definition"]["current"]

    result = modeling.split_sections(path)
    assert result["snapshot_updated"]
    listed = modeling.list_diagrams(path)
    assert listed["schema_definition"] == {"tables": 2, "current": True}
    assert listed["diagrams"] == [{"name": "Main", "figures": 4, "connections": 1}]
    # The snapshot now says where each object is written
    schema = modeling.get_diagram_rows(path)[0]["document"]
    assert "sections/150-20_views.sql" in schema["files"]


@needs_lsp
def test_a_stale_snapshot_is_left_stale(tmp_path):
    from msm_plugin.lib import modeling

    path = make_project(tmp_path)
    modeling.create_diagram(path, "Main")
    dev_file = management.get_schema_development_file_path(path)
    management.set_section_sql_content("170", "\nCREATE ROLE IF NOT EXISTS `r`;\n", dev_file)
    management.set_section_sql_content(
        "140", "\n" + SECTION_140 + "\nCREATE TABLE `extra` (`i` INT);\n", dev_file)
    assert not modeling.list_diagrams(path)["schema_definition"]["current"]

    assert not modeling.split_sections(path)["snapshot_updated"]
    assert modeling.list_diagrams(path)["schema_definition"]["tables"] == 2


@needs_lsp
def test_a_statement_that_runs_into_the_next_section_is_reported(tmp_path):
    path = make_project(tmp_path)
    dev_file = management.get_schema_development_file_path(path)
    management.set_section_sql_content(
        "140", "\nCREATE TABLE a (i INT);\nCREATE TABLE b (i INT)\n", dev_file)
    original = dev_script(path)

    result = sections.split_sections(path, ["140"])
    assert result.split == {} and result.files == []
    assert result.skipped["140"].endswith("delimiter `;`: CREATE TABLE b (i INT)")
    assert dev_script(path) == original


@needs_lsp
def test_a_script_lacking_a_section_gets_a_current_snapshot(tmp_path):
    from msm_plugin.lib import modeling

    path = make_project(tmp_path)
    dev_file = management.get_schema_development_file_path(path)
    complete = management.get_file_sections(dev_file)
    management.write_sections_to_file(
        dev_file, {k: v for k, v in complete.items() if k != "180"}, overwrite_existing=True)

    modeling.update_schema_definition(path)
    assert "MSM Section 180" in dev_script(path)
    assert modeling.list_diagrams(path)["schema_definition"]["current"]


def test_splitting_needs_the_binary(tmp_path, monkeypatch):
    path = make_project(tmp_path)
    monkeypatch.setattr(lsp, "find_lsp", lambda lsp_path=None: None)
    with pytest.raises(lsp.LspError, match="could not be found"):
        sections.split_sections(path)


# -----------------------------------------------------------------------------
# A complete script


def test_missing_template_sections_are_added(tmp_path):
    path = make_project(tmp_path)
    dev_file = management.get_schema_development_file_path(path)
    complete = management.get_file_sections(dev_file)
    lacking = {k: v for k, v in complete.items() if k not in ("120", "180", "910")}
    management.write_sections_to_file(dev_file, lacking, overwrite_existing=True)

    filled, added = sections.complete_sections(path, management.get_file_sections(dev_file))
    assert added == ["120", "180", "910"]
    assert list(filled) == list(complete)
    assert "SELECT 0, 0, 1;" in filled["910"]["full_content"]
    assert "USE `shop`;" in filled["910"]["full_content"]
    management.write_sections_to_file(dev_file, filled, overwrite_existing=True)
    assert dev_script(path).endswith("SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;\n")
    assert "\n\n\n-- ####" in dev_script(path)
    # Nothing to add: the sections come back as they were
    sections_now = management.get_file_sections(dev_file)
    assert sections.complete_sections(path, sections_now) == (sections_now, [])


@needs_lsp
def test_a_split_project_deploys(sandbox_session, tmp_path):
    from msm_plugin.management import deploy_schema, generate_deployment_script, prepare_release

    schema_name = "msm_split_test"
    path = make_project(tmp_path, schema_name)
    sections.split_sections(path)
    prepare_release(schema_project_path=path, version="1.0.0", next_version="1.0.1")
    generate_deployment_script(schema_project_path=path, version="1.0.0")

    def query(sql):
        return helpers.query(sandbox_session, sql)

    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{schema_name}`")
    try:
        # The view was written before the function it calls; split, it comes after
        deploy_schema(schema_project_path=path, version="1.0.0")
        assert [row["name"] for row in query(
            f"SELECT `name` FROM `{schema_name}`.`customer_names`")] == ["ANN"]
        sandbox_session.run_sql(f"INSERT INTO `{schema_name}`.`customer` VALUES (2, ' Bo ')")
        assert [row["name"] for row in query(
            f"SELECT `name` FROM `{schema_name}`.`customer` WHERE `id` = 2")] == ["Bo"]
        assert [row["EVENT_NAME"] for row in query(
            "SELECT EVENT_NAME FROM information_schema.EVENTS "
            f"WHERE EVENT_SCHEMA = '{schema_name}'")] == ["msm_split_nightly"]
        assert [row["CONSTRAINT_NAME"] for row in query(
            "SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
            f"WHERE CONSTRAINT_SCHEMA = '{schema_name}'")] == ["fk_orders_customer"]
    finally:
        sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{schema_name}`")
        sandbox_session.run_sql("DROP ROLE IF EXISTS `shop_reader`")
