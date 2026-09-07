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

"""End-to-end migration of a real MySQL instance to a real MariaDB instance.

This is the only test that drives the whole migrator feature at once, against
two servers it deploys itself, with nothing stubbed. It runs the same sequence
an operator would:

1. Two sandboxes are deployed with ``sandbox.deploy``: the SOURCE from an
   installed **MySQL** ``mysqld`` (:data:`MYSQL_SERVER_BINARY`), the TARGET from
   the MariaDB server on the PATH. A MySQL source is the point of the exercise,
   so the MariaDB server cannot stand in for it - the test skips when there is
   no MySQL server to deploy from.
2. Both instances are registered as MCP connections through the ``mcp setup``
   COMMAND LINE (``--deleteConnections`` then ``--addConnection`` /
   ``--passwordEnv``), not through the interactive menu. ``sandbox.deploy``
   registers them itself, so they are deleted first and the command line is
   what really configures them.
3. The schema to migrate - tables with a foreign key, a view, a trigger and a
   procedure, plus rows - is created on the source through the ``db.*`` tools.
4. The migration tooling is installed with ``mcp setup --installMigrator``.
5. ``migrator.set_config`` writes ``config/migration.yaml`` and
   ``migrator.run`` performs the migration.
6. The target is inspected through the ``db.*`` tools: the schema, every object
   in it and every row have to have arrived.

Only modes 1 (``one_step``) and 3 (``staged``) of the tooling work at present,
and this covers mode 1 - a serial ``mysqldump | mariadb`` stream. Mode 3 is NOT
covered because it cannot run here at all: ``scripts/25_staged_dump.sh`` uses
``declare -A``, so it needs bash 4.0 or later, and macOS ships bash 3.2.

Three things about the tooling are accounted for here rather than rediscovered
on every run, each documented where it is dealt with:

* the dump tool has to be an upstream ``mysqldump`` (:func:`_mysql_dump_binary`),
* a sandbox has only a root account, which the tooling refuses to migrate as
  unless ``ALLOW_ROOT_USERS`` says otherwise (:func:`_migration_env`),
* and without ``pv`` the mode 1 script's progress fallback misreports a
  successful migration on bash 3.2 (:func:`_unsupported_reason`).
"""

# cSpell:ignore mysqlsh MariaDB mysqld mysqldump mariadbd mcpserver migrator

import asyncio
import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

# Not part of a normal run: this deploys two servers, reaches the network and
# installs the migration tooling, so it only runs when a run asks for it with
# --e2e (see the pytest_collection_modifyitems hook in tests/conftest.py). The
# mark is module-level so anything added to this file inherits it.
pytestmark = pytest.mark.e2e

from mcp_plugin.lib import config, general, migrator_functions, setup_migrator
import mcp_plugin.tests.unit.helpers as helpers

# The MySQL server the SOURCE sandbox is deployed from, and the MySQL client's
# own dump tool beside it. Overridable so a machine that keeps MySQL somewhere
# else can still run this.
MYSQL_SERVER_BINARY = os.environ.get("MCP_TEST_MYSQLD", "/usr/local/mysql/bin/mysqld")

# The migration mode under test: mode 1, a single mysqldump-to-mariadb stream.
MIGRATION_MODE = "one_step"

# The schema created on the source and expected on the target afterwards.
MIGRATED_SCHEMA = "mcp_e2e_migration"

# The artifacts directory the run writes into, relative to the install. It is
# emptied before every run and removed afterwards: the orchestrator is
# resume-safe, so a state.json left by an earlier run makes the next one SKIP
# every step it already recorded as DONE - and then migrate nothing at all.
MIGRATION_OUT_DIR = os.path.join("artifacts", "pytest_e2e_migration")

# The root password both sandboxes are deployed with, and the environment
# variable it reaches `mcp setup --addConnection` through - the NAME is what is
# passed, so the password itself never appears in a command line.
SANDBOX_PASSWORD = "mcp_e2e_migration_root"
PASSWORD_ENV_VAR = "MCP_E2E_MIGRATION_PASSWORD"

# A deploy initializes a data directory and starts a server; an install fetches
# a release and pip-installs into a fresh virtual environment; a migration runs
# a chain of shell scripts. None of them fit a normal tool-call timeout.
DEPLOY_TIMEOUT = 300
SETUP_TIMEOUT = 600
MIGRATION_TIMEOUT = 600

# The schema to migrate. Every object type the dump carries is represented, so
# that --routines and --triggers are exercised and not just the table data. The
# trigger and the procedure bodies are deliberately SINGLE statements: a
# BEGIN ... END body would depend on how db.execute_sql_script splits the
# script, which is not what this test is about. Names are fully qualified for
# the same reason - nothing here relies on a USE taking effect.
SOURCE_SCHEMA_SQL = f"""
CREATE DATABASE {MIGRATED_SCHEMA};

CREATE TABLE {MIGRATED_SCHEMA}.customers (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(80) NOT NULL,
    joined DATE NOT NULL
);

CREATE TABLE {MIGRATED_SCHEMA}.orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    total DECIMAL(10,2) NOT NULL,
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
        REFERENCES {MIGRATED_SCHEMA}.customers (id)
);

CREATE VIEW {MIGRATED_SCHEMA}.customer_totals AS
    SELECT c.id AS id, c.name AS name, COALESCE(SUM(o.total), 0) AS total
    FROM {MIGRATED_SCHEMA}.customers c
    LEFT JOIN {MIGRATED_SCHEMA}.orders o ON o.customer_id = c.id
    GROUP BY c.id, c.name;

CREATE TRIGGER {MIGRATED_SCHEMA}.orders_before_insert
    BEFORE INSERT ON {MIGRATED_SCHEMA}.orders
    FOR EACH ROW SET NEW.total = ROUND(NEW.total, 2);

CREATE PROCEDURE {MIGRATED_SCHEMA}.customer_count()
    SELECT COUNT(*) AS customers FROM {MIGRATED_SCHEMA}.customers;

INSERT INTO {MIGRATED_SCHEMA}.customers (name, joined) VALUES
    ('Alice', '2024-01-15'), ('Bob', '2024-02-20'), ('Chen', '2024-03-25');

INSERT INTO {MIGRATED_SCHEMA}.orders (customer_id, total) VALUES
    (1, 10.50), (1, 99.99), (2, 5.00);
"""

# What the target must hold once the migration has run: the objects of each
# type, and the rows of each table.
EXPECTED_OBJECTS = {
    "table": ["customers", "orders"],
    "view": ["customer_totals"],
    "trigger": ["orders_before_insert"],
    "procedure": ["customer_count"],
}

EXPECTED_CUSTOMERS = [
    {"id": 1, "name": "Alice", "joined": "2024-01-15"},
    {"id": 2, "name": "Bob", "joined": "2024-02-20"},
    {"id": 3, "name": "Chen", "joined": "2024-03-25"},
]

# Decimals arrive as text (see db_functions._serialize_result), so the expected
# totals are the strings the server formats them as.
EXPECTED_ORDERS = [
    {"id": 1, "customer_id": 1, "total": "10.50"},
    {"id": 2, "customer_id": 1, "total": "99.99"},
    {"id": 3, "customer_id": 2, "total": "5.00"},
]

EXPECTED_TOTALS = [
    {"id": 1, "name": "Alice", "total": "110.49"},
    {"id": 2, "name": "Bob", "total": "5.00"},
    {"id": 3, "name": "Chen", "total": "0.00"},
]


def _mysql_dump_binary() -> str:
    """Returns the MySQL client's dump tool, beside the MySQL server binary.

    The tooling defaults to ``mariadb-dump``, which cannot dump a MySQL 8.4+
    source: it issues ``SHOW PACKAGE STATUS``, which MySQL rejects as a syntax
    error. The tooling's own preflight says so and tells the operator to point
    ``MARIADB_DUMP_BIN`` at an upstream mysqldump, which is what the
    configuration below does.
    """
    return os.path.join(os.path.dirname(MYSQL_SERVER_BINARY), "mysqldump")


def _bash_major_version():
    """Returns the major version of the bash the tooling's scripts run under.

    Returns:
        The major version as an int, or None when it cannot be determined.
    """
    bash = shutil.which("bash")
    if bash is None:
        return None

    try:
        completed = subprocess.run(
            [bash, "-c", "echo ${BASH_VERSINFO[0]}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    version = completed.stdout.strip()

    return int(version) if version.isdigit() else None


def _unsupported_reason():
    """Returns why this machine cannot run the migration, or None if it can.

    Returns:
        A skip reason, or None when everything the migration needs is present.
    """
    if not os.path.isfile(MYSQL_SERVER_BINARY):
        return (
            f"No MySQL server at '{MYSQL_SERVER_BINARY}' to deploy the migration "
            "source from (set MCP_TEST_MYSQLD to point at one)."
        )
    if not os.path.isfile(_mysql_dump_binary()):
        return (
            f"No mysqldump at '{_mysql_dump_binary()}': a MariaDB mariadb-dump "
            "cannot dump a MySQL 8.4+ source."
        )
    if not helpers.server_binary_available():
        return "No MariaDB server binary available to deploy the migration target."
    if not setup_migrator.is_supported():
        return "The migration tooling does not run on this platform."

    bash_version = _bash_major_version()
    if shutil.which("pv") is None and bash_version is not None and bash_version < 4:
        # Without pv, scripts/10_one_step_migration.sh falls back to a
        # background heartbeat and kills it from an EXIT trap. On bash 3.2 the
        # trap's `wait` both holds the script's stdout open for a further 60s
        # and makes it exit 143, so a migration that copied everything
        # correctly is still reported as failed. pv is documented as an
        # optional dependency of the tooling; here it is what keeps mode 1 from
        # tripping over that.
        return (
            "pv is not installed and bash is older than 4.0, so the tooling's "
            "no-pv fallback would report a successful migration as failed."
        )

    return None


def _rows(result) -> list:
    """Returns a tool's list payload as a list.

    ``helpers.tool_payload`` collapses a single-element list into the bare
    element, so a listing with exactly one row is not a list and cannot be
    iterated over as one.
    """
    payload = helpers.tool_payload(result) or []
    if isinstance(payload, dict):
        return [payload]

    return payload


def _setup_cli(*options, password=None) -> str:
    """Runs ``mcp setup`` with the given options in a mariadb-shell subprocess.

    Args:
        options (str): The command-line options to pass.
        password (str): A password to place in :data:`PASSWORD_ENV_VAR` for
            ``--passwordEnv`` to read, rather than in the command line.

    Returns:
        The command's standard output.

    Raises:
        AssertionError: If the command failed.
    """
    child_env = os.environ.copy()
    if password is not None:
        child_env[PASSWORD_ENV_VAR] = password

    completed = subprocess.run(
        [helpers.shell_binary(), "--quiet-start=2", "--", "mcp", "setup", *options],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=SETUP_TIMEOUT,
        check=False,
    )

    assert completed.returncode == 0, (
        f"'mcp setup {' '.join(options)}' failed with {completed.returncode}:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )

    return completed.stdout


def _deploy_sandbox(port, sandbox_dir, mariadbd_path=None) -> None:
    """Deploys one sandbox instance through the sandbox.deploy MCP tool.

    Args:
        port (int): The port to deploy on.
        sandbox_dir (str): The directory to deploy into.
        mariadbd_path (str): The server binary to deploy from. None uses the
            one on the PATH, which is the MariaDB server here.
    """
    arguments = {
        "port": port,
        "sandbox_dir": sandbox_dir,
        "password": SANDBOX_PASSWORD,
        # TLS is off for the same reason the other sandbox tests turn it off:
        # certificate generation would drag openssl into a local test.
        "ssl": False,
    }
    if mariadbd_path is not None:
        arguments["mariadbd_path"] = mariadbd_path

    result = helpers.call_tool(
        function_groups=["sandbox"],
        tool_name="sandbox.deploy",
        arguments=arguments,
        timeout=DEPLOY_TIMEOUT,
    )

    assert result.is_error is False, helpers.tool_payload(result)


@pytest.fixture
def migration_instances(clean_config):
    """Deploys the MySQL source and the MariaDB target for one migration.

    ``clean_config`` backs up and restores the connections and the allowed
    paths, so the two sandbox connections registered here (by ``sandbox.deploy``
    and then by the setup command line) are removed again afterwards.

    Yields:
        A namespace with the two ports, their connection URIs, the password and
        the sandbox directory.
    """
    reason = _unsupported_reason()
    if reason is not None:
        pytest.skip(reason)

    sandbox_dir = os.path.abspath(tempfile.mkdtemp(prefix="mcp_migration_"))
    config.set_allowed_paths(config.get_allowed_paths() + [sandbox_dir])

    source_port = helpers.find_free_port()
    target_port = helpers.find_free_port()
    while target_port == source_port:
        # find_free_port binds and closes, so two calls can name one port.
        target_port = helpers.find_free_port()

    instances = SimpleNamespace(
        sandbox_dir=sandbox_dir,
        password=SANDBOX_PASSWORD,
        source_port=source_port,
        target_port=target_port,
        source_uri=f"root@127.0.0.1:{source_port}",
        target_uri=f"root@127.0.0.1:{target_port}",
    )

    try:
        _deploy_sandbox(source_port, sandbox_dir, mariadbd_path=MYSQL_SERVER_BINARY)
        _deploy_sandbox(target_port, sandbox_dir)

        yield instances
    finally:
        for port in (source_port, target_port):
            for tool_name, arguments in (
                (
                    "sandbox.stop",
                    {
                        "port": port,
                        "sandbox_dir": sandbox_dir,
                        "password": SANDBOX_PASSWORD,
                    },
                ),
                ("sandbox.delete", {"port": port, "sandbox_dir": sandbox_dir}),
            ):
                try:
                    helpers.call_tool(["sandbox"], tool_name, arguments)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass

        shutil.rmtree(sandbox_dir, ignore_errors=True)


@pytest.fixture
def migrator_install():
    """Installs the migration tooling with ``mcp setup --installMigrator``.

    An install that was already on the machine is reused and left in place -
    the download reaches the network and pip, and a developer's existing copy
    is not this test's to replace. One performed here is removed again, so a
    machine that never had the tooling does not end up with a release and a
    ~/.local/bin wrapper because the suite ran.

    Either way the tooling's own ``config/migration.yaml`` is restored, since
    ``migrator.set_config`` overwrites it.

    Yields:
        The install directory.
    """
    installed_here = not setup_migrator.is_installed()
    if installed_here:
        _setup_cli("--installMigrator")

    assert setup_migrator.is_installed(), (
        "The migration tooling is not installed after "
        "'mcp setup --installMigrator'."
    )

    install_dir = general.get_migrator_path()
    config_path = os.path.join(install_dir, migrator_functions.MIGRATION_CONFIG)
    original_config = None
    if os.path.isfile(config_path):
        with open(config_path, "rb") as handle:
            original_config = handle.read()

    # Cleared BEFORE the run, not only after it: a state.json an earlier run
    # left behind would otherwise make this one skip every step and migrate
    # nothing, while still reporting a clean report.
    out_path = os.path.join(install_dir, MIGRATION_OUT_DIR)
    shutil.rmtree(out_path, ignore_errors=True)

    try:
        yield install_dir
    finally:
        shutil.rmtree(out_path, ignore_errors=True)

        if installed_here:
            _setup_cli("--removeMigrator")
        elif original_config is not None:
            with open(config_path, "wb") as handle:
                handle.write(original_config)
        elif os.path.isfile(config_path):
            os.remove(config_path)


def _migration_env(instances) -> dict:
    """Returns the tooling's environment mapping for this migration.

    No password appears here: ``migrator.set_config`` refuses them and reads
    each from the configured connection its user/host/port fields name.
    """
    return {
        # Source: the MySQL sandbox.
        "SRC_HOST": "127.0.0.1",
        "SRC_PORT": str(instances.source_port),
        "SRC_ADMIN_USER": "root",
        "SRC_USER": "root",
        "SRC_DBS": MIGRATED_SCHEMA,
        "SRC_SSL_MODE": "DISABLED",
        # See _mysql_dump_binary: mariadb-dump cannot dump this source.
        "MARIADB_DUMP_BIN": _mysql_dump_binary(),
        # Target: the MariaDB sandbox.
        "TGT_HOST": "127.0.0.1",
        "TGT_PORT": str(instances.target_port),
        "TGT_ADMIN_USER": "root",
        "TGT_USER": "root",
        # A sandbox has no account but root, and the tooling refuses to migrate
        # as root unless it is told to.
        "ALLOW_ROOT_USERS": "1",
        # Nothing to carry over: the schema owns no accounts of its own.
        "MIGRATE_APP_USERS": "0",
        "ANALYZE_TARGET": "1",
    }


async def _create_source_schema(call, instances) -> None:
    """Creates the schema to migrate on the source instance."""
    connect_result = await call("db.connect", {"uri": instances.source_uri})
    assert connect_result.is_error is False, helpers.tool_payload(connect_result)
    connection_id = helpers.tool_payload(connect_result)

    script_result = await call(
        "db.execute_sql_script",
        {"connection_id": connection_id, "sql_script": SOURCE_SCHEMA_SQL},
    )
    assert script_result.is_error is False, helpers.tool_payload(script_result)

    # The rows have to be there before the migration reads them; the trigger
    # rounds the totals on the way in, so this also proves it fired.
    orders = await call(
        "db.execute_sql",
        {
            "connection_id": connection_id,
            "sql": f"SELECT COUNT(*) AS orders FROM {MIGRATED_SCHEMA}.orders",
        },
    )
    assert helpers.tool_payload(orders)["rows"] == [{"orders": 3}]

    close_result = await call("db.close", {"connection_id": connection_id})
    assert close_result.is_error is False, helpers.tool_payload(close_result)


async def _verify_target(call, instances) -> None:
    """Checks that everything the source held arrived on the target."""
    connect_result = await call("db.connect", {"uri": instances.target_uri})
    assert connect_result.is_error is False, helpers.tool_payload(connect_result)
    connection_id = helpers.tool_payload(connect_result)

    schemas = [
        row["schema_name"]
        for row in _rows(await call("db.list_schemas", {"connection_id": connection_id}))
    ]
    assert MIGRATED_SCHEMA in schemas, (
        f"'{MIGRATED_SCHEMA}' is not on the target. Schemas: {schemas}"
    )

    for object_type, expected in EXPECTED_OBJECTS.items():
        listed = [
            row["name"]
            for row in _rows(
                await call(
                    "db.list_objects",
                    {
                        "connection_id": connection_id,
                        "schema_name": MIGRATED_SCHEMA,
                        "object_type": object_type,
                    },
                )
            )
        ]
        assert listed == expected, f"{object_type}s on the target: {listed}"

    # The foreign key came across as a constraint, not just as a column.
    details = helpers.tool_payload(
        await call(
            "db.get_object_details",
            {
                "connection_id": connection_id,
                "schema_name": MIGRATED_SCHEMA,
                "object_name": "orders",
                "object_type": "table",
            },
        )
    )
    references = details.get("references") or []
    constraints = [
        (reference.get("reference_mapping") or {}).get("constraint")
        for reference in references
    ]
    assert f"{MIGRATED_SCHEMA}.fk_orders_customer" in constraints, references

    # The key still maps the same column to the same one it referenced.
    mapping = next(
        reference["reference_mapping"]
        for reference in references
        if reference["reference_mapping"]["constraint"]
        == f"{MIGRATED_SCHEMA}.fk_orders_customer"
    )
    assert mapping["referenced_table"] == "customers"
    assert mapping["column_mapping"] == [{"base": "customer_id", "ref": "id"}]

    # Every row, including the view's aggregate over both tables.
    for sql, expected in (
        (f"SELECT id, name, joined FROM {MIGRATED_SCHEMA}.customers ORDER BY id",
         EXPECTED_CUSTOMERS),
        (f"SELECT id, customer_id, total FROM {MIGRATED_SCHEMA}.orders ORDER BY id",
         EXPECTED_ORDERS),
        (f"SELECT id, name, total FROM {MIGRATED_SCHEMA}.customer_totals ORDER BY id",
         EXPECTED_TOTALS),
    ):
        result = await call(
            "db.execute_sql", {"connection_id": connection_id, "sql": sql}
        )
        assert result.is_error is False, helpers.tool_payload(result)
        assert helpers.tool_payload(result)["rows"] == expected, sql

    close_result = await call("db.close", {"connection_id": connection_id})
    assert close_result.is_error is False, helpers.tool_payload(close_result)


async def _configure_and_run(instances) -> dict:
    """Writes the configuration and runs the migration over one MCP session."""
    async with helpers.mcp_session(
        ["migrator", "db"], timeout=MIGRATION_TIMEOUT
    ) as call:
        set_config_result = await call(
            "migrator.set_config",
            {"mode": MIGRATION_MODE, "env": _migration_env(instances)},
        )
        assert set_config_result.is_error is False, helpers.tool_payload(
            set_config_result
        )
        written = helpers.tool_payload(set_config_result)

        # The configuration names both sandboxes, and each account resolved to
        # the connection the setup command line stored - which is what will be
        # used to look the passwords up.
        assert written["mode"] == MIGRATION_MODE
        assert written["connections"] == {
            "SRC_ADMIN_USER": instances.source_uri,
            "SRC_USER": instances.source_uri,
            "TGT_ADMIN_USER": instances.target_uri,
            "TGT_USER": instances.target_uri,
        }
        # No password was given and none was written.
        assert not any(
            key.endswith("_PASS") for key in written["keys"]
        ), written["keys"]

        run_result = await call(
            "migrator.run",
            {
                "mode": MIGRATION_MODE,
                "out": MIGRATION_OUT_DIR,
                "timeout": MIGRATION_TIMEOUT,
            },
        )
        assert run_result.is_error is False, helpers.tool_payload(run_result)

        return helpers.tool_payload(run_result)


def test_migration_e2e(migration_instances, migrator_install):
    """Migrates a MySQL schema to MariaDB and verifies the result."""
    instances = migration_instances

    # 1. Register both instances through the setup COMMAND LINE. sandbox.deploy
    #    stored them already, so they go first and the command line is what
    #    really configures them.
    _setup_cli(
        f"--deleteConnections={instances.source_uri},{instances.target_uri}"
    )
    for uri in (instances.source_uri, instances.target_uri):
        output = _setup_cli(
            f"--addConnection={uri}",
            f"--passwordEnv={PASSWORD_ENV_VAR}",
            password=instances.password,
        )
        # Stored only after the credentials were shown to open a session.
        assert f"Connection '{uri}' stored after verification." in output, output

    configured = config.list_connection_uris()
    assert instances.source_uri in configured
    assert instances.target_uri in configured

    # 2. Create the schema to migrate on the MySQL source.
    async def _create():
        async with helpers.mcp_session(["db"]) as call:
            await _create_source_schema(call, instances)

    asyncio.run(_create())

    # 3. Configure and run the migration.
    outcome = asyncio.run(_configure_and_run(instances))

    assert outcome["succeeded"] is True, (
        f"migrator.run exited {outcome['exit_code']}:\n"
        f"{outcome['stdout']}\n{outcome['stderr']}"
    )
    assert outcome["exit_code"] == 0
    assert outcome["mode"] == MIGRATION_MODE
    assert outcome["out_dir"] == MIGRATION_OUT_DIR

    # The run reported which connections the passwords came from, and no
    # password anywhere in what the client was told.
    assert outcome["passwords_from"]["SRC_ADMIN_PASS"] == instances.source_uri
    assert outcome["passwords_from"]["TGT_ADMIN_PASS"] == instances.target_uri
    assert SANDBOX_PASSWORD not in str(outcome)

    # Every step of the tooling's own report finished.
    report = outcome["report"]
    assert report is not None, "The run wrote no report.json."
    statuses = {step["id"]: step["status"] for step in report["steps"]}
    assert set(statuses.values()) == {"DONE"}, statuses
    assert "one_step_dump_restore" in statuses

    # 4. The target really holds the migrated schema.
    async def _verify():
        async with helpers.mcp_session(["db"]) as call:
            await _verify_target(call, instances)

    asyncio.run(_verify())
