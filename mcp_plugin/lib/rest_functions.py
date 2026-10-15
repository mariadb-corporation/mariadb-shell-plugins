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

"""MCP tools for the MariaDB REST Daemon (mariadb-restd).

The daemon serves the REST services defined in a server's REST metadata.
``rest.configure_restd`` sets one up for a server and starts it - for a
sandbox in the sandbox's own folder, so that it follows the sandbox - and the
other tools start, stop, list and delete the deployments. What a deployment
is, and how its daemon is run, is in :mod:`mcp_plugin.lib.restd`.

A daemon keeps running when the MCP server ends, as a sandbox's server does.
"""

# cSpell:ignore mysqlsh MariaDB restd

import json
import os
import re
from typing import Optional

from mcp_plugin.lib import db_functions, general, restd, restd_certs, restd_install
from mcp_plugin.lib.tool_registrar import tool_error, tool_registrar

# The part of a metadata schema's name its roles are named after; the roles
# carry the schema's prefix and postfix around it.
_METADATA_BASE_NAME = restd.DEFAULT_METADATA_SCHEMA
_ROLES = ("meta_provider", "data_provider")


def _role_names(metadata_schema: str) -> list:
    """The roles of a metadata schema the daemon's account needs.

    ``acme_mariadb_rest_service_eu`` has ``acme_mariadb_rest_service_meta_provider_eu``,
    as CONFIGURE REST METADATA names them.
    """
    prefix, _, postfix = metadata_schema.partition(_METADATA_BASE_NAME)
    return [f"{prefix}{_METADATA_BASE_NAME}_{role}{postfix}" for role in _ROLES]


def _metadata_schemas(session) -> list:
    """The names of the REST metadata schemas the account can see."""
    row = session.run_sql("SHOW REST METADATA SCHEMAS FORMAT=JSON").fetch_one()
    schemas = json.loads(row[0]) if row and row[0] else []
    return [schema["schema_name"] for schema in schemas]


def _choose_metadata_schema(session, metadata_schema: Optional[str]) -> tuple:
    """Picks the metadata schema to serve.

    The one named, else the only one there is, else the default - which is
    then still to be created. Several and none named is refused: which of
    them a daemon serves is not a guess to make.

    Returns:
        tuple: The schema's name, and whether it exists.
    """
    existing = _metadata_schemas(session)
    if metadata_schema is not None:
        return metadata_schema, metadata_schema in existing
    if len(existing) == 1:
        return existing[0], True
    if not existing:
        return restd.DEFAULT_METADATA_SCHEMA, False

    raise tool_error(
        "The server has several REST metadata schemas: "
        + ", ".join(existing)
        + ". Pass metadata_schema to say which one the REST Daemon serves."
    )


def _parse_uri(uri: str) -> dict:
    """A connection URI's parts, as the shell parses it."""
    from mysqlsh.globals import shell

    return dict(shell.parse_uri(uri))


def _open_session(connection: dict, password: str):
    """Opens a session of its own on the connection, with this password."""
    from mysqlsh.globals import shell

    return shell.open_session({**connection, "password": password})


def _grants(session) -> list:
    return [row[0] for row in session.run_sql("SHOW GRANTS").fetch_all()]


_GRANT_ON = re.compile(r"^GRANT (?P<privileges>.+?) ON (?P<object>\S+) TO ")
_GRANT_ROLE = re.compile(r"^GRANT (?P<role>\S+) TO ")


def _schema_pattern_matches(pattern: str, schema: str) -> bool:
    """Whether a schema-level grant's database covers a schema.

    The server reads it as a LIKE pattern: `_` and `%` are wildcards, and
    `\\_`, `\\%` - how a grant on a name with underscores is usually
    written, so that they are not - are the characters themselves.
    """
    expression = ""
    escaped = False
    for char in pattern:
        if escaped:
            expression += re.escape(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "%":
            expression += ".*"
        elif char == "_":
            expression += "."
        else:
            expression += re.escape(char)
    return re.fullmatch(expression, schema, re.IGNORECASE | re.DOTALL) is not None


def _missing_privileges(grants: list, metadata_schema: str) -> list:
    """What the account lacks of what ``mariadb-restd --setup`` needs.

    The setup registers the daemon in the metadata, creates an account and
    grants it the metadata schema's roles, so it needs SELECT, INSERT and
    UPDATE on the schema, CREATE USER with the GRANT OPTION, and both roles
    WITH ADMIN OPTION. Read from SHOW GRANTS, which lists what the active role
    gives as well; ALL PRIVILEGES on everything WITH GRANT OPTION covers it
    all, as it does for root.

    Args:
        grants (list): The lines of SHOW GRANTS.
        metadata_schema (str): The metadata schema.

    Returns:
        list: What is missing, as text; empty when nothing is.
    """
    # name -> privileges, for the global level and the schema's; the roles
    # held WITH ADMIN OPTION
    on_global, on_schema, roles = set(), set(), set()
    global_grant_option = False
    for grant in grants:
        text = " ".join(grant.replace("`", "").split())
        privileges_grant = _GRANT_ON.match(text)
        if privileges_grant:
            privileges = {
                privilege.strip().upper()
                for privilege in privileges_grant["privileges"].split(",")
            }
            target = privileges_grant["object"].lower()
            if target == "*.*":
                on_global |= privileges
                global_grant_option |= text.upper().endswith("WITH GRANT OPTION")
            elif target.endswith(".*") and _schema_pattern_matches(
                target[:-2], metadata_schema
            ):
                on_schema |= privileges
            continue
        role_grant = _GRANT_ROLE.match(text)
        if role_grant and text.upper().endswith("WITH ADMIN OPTION"):
            roles.add(role_grant["role"].lower())

    if "ALL PRIVILEGES" in on_global and global_grant_option:
        return []

    missing = []
    if "CREATE USER" not in on_global and "ALL PRIVILEGES" not in on_global:
        missing.append("CREATE USER on *.*")
    if not global_grant_option:
        missing.append("the GRANT OPTION on *.*")

    held = on_global | on_schema
    for privilege in ("SELECT", "INSERT", "UPDATE"):
        if privilege not in held and "ALL PRIVILEGES" not in held:
            missing.append(f"{privilege} on {metadata_schema}.*")

    for role in _role_names(metadata_schema):
        if role.lower() not in roles:
            missing.append(f"the role {role} WITH ADMIN OPTION")

    return missing


def register_rest_tools(server, function_groups=()) -> None:
    """Registers the REST Daemon tools on the given server.

    Starting, stopping, listing and deleting deployments works on their
    folders and is always registered; rest.configure_restd sets one up for a
    connection opened with db.connect, so it is registered only together with
    the db group.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    import anyio.to_thread
    from mcp.server.mcpserver import Context
    from mysqlsh.globals import shell

    tool = tool_registrar(server)

    def _sandbox_base_dir(sandbox_dir: Optional[str] = None) -> str:
        return os.path.abspath(
            os.path.expanduser(sandbox_dir or shell.options["sandboxDir"])
        )

    async def _require_deployment_access(ctx, directory: str) -> None:
        """Authorizes a deployment folder a tool is to work on.

        One the server lists - a sandbox's in the default sandbox path, or
        one in the deployments root, where rest.configure_restd puts them
        without asking - needs no allowed path: refusing what the server
        made itself would only have the agent ask the user to trust a
        folder they never chose. Any other folder is checked as every
        path argument is.
        """
        resolved = os.path.abspath(os.path.expanduser(directory))
        if resolved in restd.deployment_dirs(_sandbox_base_dir()):
            return
        await general.require_allowed_path(ctx, directory)

    @tool(name="rest.get_restd_status")
    def get_restd_status() -> list:
        """Lists the REST Daemon deployments, and whether each is running.

        The deployments of the sandboxes in the default sandbox path and
        those set up for other servers.

        Returns:
            One object per deployment, with its 'directory', the 'host' and
            'server_port' of the server it serves, its own 'port' and 'url',
            'status' ('running' or 'stopped'), 'pid' (None when stopped),
            'log_path', 'metadata_schema', 'connection_uri' (the connection
            it was set up with) and 'sandbox_port' (None when it is not a
            sandbox's).
        """
        return [
            restd.status(directory)
            for directory in restd.deployment_dirs(_sandbox_base_dir())
        ]

    @tool(name="rest.start_restd")
    async def start_restd(
        ctx: Context,
        directory: str,
        restd_path: Optional[str] = None,
    ) -> dict:
        """Starts the REST Daemon of a deployment.

        Nothing is done when it is running already. It keeps running after
        the MCP server ends.

        Args:
            directory: The deployment's folder, as rest.get_restd_status lists
                it.
            restd_path: The mariadb-restd executable to run, or its folder.
                Found on the PATH, in ~/.local/bin or among the installed
                releases when empty.

        Returns:
            The deployment's status, as rest.get_restd_status reports it.
        """
        await _require_deployment_access(ctx, directory)
        await general.require_allowed_path(ctx, restd_path)

        def work():
            executable = restd.find_restd(restd_path)
            return restd.start(executable, directory)

        return await anyio.to_thread.run_sync(work)

    @tool(name="rest.stop_restd")
    async def stop_restd(ctx: Context, directory: str) -> dict:
        """Stops the REST Daemon of a deployment.

        Nothing is done when it is stopped already. A daemon that does not
        end within 30 seconds is killed.

        Args:
            directory: The deployment's folder, as rest.get_restd_status lists
                it.

        Returns:
            The deployment's status, as rest.get_restd_status reports it.
        """
        await _require_deployment_access(ctx, directory)
        return await anyio.to_thread.run_sync(lambda: restd.stop(directory))

    @tool(name="rest.get_certificate_status")
    def get_certificate_status() -> dict:
        """Says whether this system trusts the REST Daemons' HTTPS certificate.

        Asks the system itself, so a certificate that is stored but not
        trusted counts as not trusted. Makes nothing: before the first REST
        Daemon setup there is no certificate yet.

        Returns:
            'exists' (whether the first setup made it), 'trusted', and
            'ca_path', the local certificate authority's certificate.
        """
        ca_path = restd_certs.ca_path()
        exists = os.path.isfile(ca_path)
        return {
            "exists": exists,
            "trusted": exists and restd_certs.is_trusted(),
            "ca_path": ca_path,
        }

    @tool(name="rest.trust_certificate")
    async def trust_certificate(ctx: Context) -> dict:
        """Has this system trust the HTTPS certificate of the REST Daemons.

        Every REST Daemon on this machine serves one certificate, made by the
        first setup and signed by a local certificate authority. Trusting
        that authority once makes browsers accept every daemon's
        https://localhost address. It is added to the user's own store - the
        login keychain on macOS, the Root store on Windows, the NSS database
        on Linux - and macOS and Windows ask the user to confirm in a system
        dialog. Nothing is done where it is trusted already.

        Returns:
            'trusted' (True), 'changed' (whether it was added now) and
            'ca_path', the authority's certificate.
        """
        def trusted():
            restd_certs.ensure()
            return restd_certs.is_trusted()

        if await anyio.to_thread.run_sync(trusted):
            return {"trusted": True, "changed": False,
                    "ca_path": restd_certs.ca_path()}
        # A trusted root is the user's to grant; Linux asks nothing itself.
        if not general.is_gui_mode() and not await _confirm_trust(ctx):
            raise tool_error("The certificate was not trusted: the user did not confirm.")

        return await anyio.to_thread.run_sync(restd_certs.trust)

    @tool(name="rest.delete_restd_config")
    async def delete_restd_config(
        ctx: Context,
        directory: str,
        connection_id: Optional[str] = None,
        drop_accounts: bool = True,
    ) -> str:
        """Deletes a REST Daemon deployment.

        The daemon is stopped, the server account the setup created for it is
        dropped together with its registration in the REST metadata, and the
        deployment's folder is deleted.

        Args:
            directory: The deployment's folder, as rest.get_restd_status lists
                it.
            connection_id: A connection to the deployment's server, opened
                with db.connect, to drop the account with. Needed unless
                drop_accounts is False.
            drop_accounts: Whether to drop the account and the registration.
                Pass False to delete the folder only, when the server is gone
                or will be.

        Returns:
            A message saying what was deleted.
        """
        await _require_deployment_access(ctx, directory)
        if not restd.is_deployment(directory):
            raise tool_error(f"'{directory}' is no {restd.RESTD_NAME} deployment.")

        client = general.get_client_identity(ctx)

        def work():
            deployment = restd.read_deployment(directory)
            notes = []
            if drop_accounts:
                if connection_id is None:
                    raise tool_error(
                        "Pass the connection_id of a connection to "
                        f"{deployment['host']}:{deployment['server_port']} to "
                        "drop the REST Daemon's account with, or "
                        "drop_accounts=False to delete the folder only."
                    )
                notes = _drop_server_objects(deployment, connection_id, client)

            restd.delete_directory(directory)
            return " ".join([f"Deleted the REST Daemon deployment '{directory}'."] + notes)

        return await anyio.to_thread.run_sync(work)

    def _drop_server_objects(deployment, connection_id, client) -> list:
        """Drops a deployment's account and registration; says what failed."""
        notes = []
        schema = db_functions.quote_identifier(deployment["metadata_schema"])
        with db_functions.use_session(connection_id, client) as session:
            for account in restd.generated_accounts(deployment):
                # A generated name is letters, digits and _, safe to quote so.
                session.run_sql(f"DROP USER IF EXISTS '{account}'@'%'")
            if deployment["rest_daemon_id"]:
                try:
                    session.run_sql(
                        f"DELETE FROM {schema}.rest_daemon WHERE id = ?",
                        [deployment["rest_daemon_id"]],
                    )
                except Exception as error:  # noqa: BLE001 - reported, not fatal
                    notes.append(
                        "Its registration in the REST metadata was kept: "
                        f"{error}"
                    )
        return notes

    # Registered only together with the db group, and last (see server.md):
    # it sets a daemon up for a connection opened with db.connect.
    if general.FUNCTION_GROUP_DB in function_groups:
        _register_configure_restd(tool, Context, anyio, _sandbox_base_dir)


async def _confirm_trust(ctx) -> bool:
    """Asks the user, via MCP elicitation, whether to trust the local CA."""
    return await general.confirm(
        ctx,
        "Have this system trust the local certificate authority of the "
        "MariaDB REST Daemons, so that browsers accept their "
        f"https://localhost addresses? ({restd_certs.certs_dir()})",
        "trust",
        "Trust the REST Daemons' local certificate authority?",
    )


async def _confirm_install(ctx) -> bool:
    """Asks the user, via MCP elicitation, whether to install mariadb-restd."""
    return await general.confirm(
        ctx,
        "The MariaDB REST Daemon (mariadb-restd) is not installed. "
        "Download and install its latest release into "
        f"{restd.install_prefix()}?",
        "install",
        "Download and install the latest mariadb-restd release?",
    )


def _register_configure_restd(tool, Context, anyio, sandbox_base_dir) -> None:
    """Registers rest.configure_restd."""

    @tool(name="rest.configure_restd")
    async def configure_restd(
        ctx: Context,
        connection_id: str,
        password: Optional[str] = None,
        sandbox_port: Optional[int] = None,
        sandbox_dir: Optional[str] = None,
        port: Optional[int] = None,
        metadata_schema: Optional[str] = None,
        restd_path: Optional[str] = None,
    ) -> dict:
        """Sets up a MariaDB REST Daemon for a server and starts it.

        In this order: the account of the connection is checked; the REST
        metadata is configured if the server has none; mariadb-restd is
        installed if it cannot be found; `mariadb-restd --setup` creates the
        deployment - an account of its own on the server, a keyring, a TLS
        certificate and its configuration - and the daemon is started, to keep
        running after the MCP server ends.

        For a sandbox the deployment is in the sandbox's folder and follows
        it: sandbox.start, sandbox.stop and sandbox.delete start, stop and
        delete it too. Otherwise it is in ~/.mariadb-restd/<host>_<port>.

        Args:
            connection_id: A connection to the server, opened with db.connect.
                Its account needs CREATE USER WITH GRANT OPTION, SELECT, INSERT
                and UPDATE on the metadata schema and its roles WITH ADMIN
                OPTION - or, when the server has no REST metadata yet, the
                privileges to create it.
            password: The account's password. The connection's stored one when
                empty.
            sandbox_port: The port of the sandbox the server is, for a
                sandbox's daemon.
            sandbox_dir: Path the sandbox is in. The default sandbox path when
                empty.
            port: The port the daemon serves HTTPS on. The first free one
                from 8443 up when empty.
            metadata_schema: The REST metadata schema to serve. Needed only
                when the server has several; the one there is otherwise, or
                mariadb_rest_service, which is created when there is none.
            restd_path: The mariadb-restd executable to use, or its folder.

        Returns:
            The deployment's status, as rest.get_restd_status reports it, plus
            'installed' (whether mariadb-restd was installed for it) and
            'setup_output', what the setup printed.
        """
        await general.require_allowed_path(ctx, sandbox_dir)
        await general.require_allowed_path(ctx, restd_path)

        client = general.get_client_identity(ctx)
        base_dir = sandbox_base_dir(sandbox_dir)

        def prepare():
            uri, stored_password = db_functions.connection_credentials(
                connection_id, client
            )
            connection = _parse_uri(uri)
            secret = stored_password if password is None else password
            if sandbox_port is not None and connection.get("port") != sandbox_port:
                raise tool_error(
                    f"The connection is to port {connection.get('port')}, not "
                    f"to the sandbox on port {sandbox_port}."
                )

            # Opened with the password given rather than the connection's own
            # session: that password is the one the setup gets, so it is the
            # one that has to work.
            try:
                session = _open_session(connection, secret)
            except Exception as error:  # noqa: BLE001 - reported with its cause
                raise tool_error(
                    f"Could not connect as {connection.get('user')}: {error}"
                ) from error
            try:
                schema, exists = _choose_metadata_schema(session, metadata_schema)
            finally:
                session.close()

            return uri, connection, secret, schema, exists

        uri, connection, secret, schema, exists = await anyio.to_thread.run_sync(
            prepare
        )

        executable = await anyio.to_thread.run_sync(restd.find_restd, restd_path)
        installed = False
        if executable is None:
            if not general.is_gui_mode() and not await _confirm_install(ctx):
                raise tool_error(
                    f"{restd.RESTD_NAME} was not found in "
                    f"{restd.searched_places()}, and was not installed. "
                    "Install it, or pass restd_path."
                )
            await anyio.to_thread.run_sync(restd_install.install)
            executable = await anyio.to_thread.run_sync(restd.find_restd)
            if executable is None:
                raise tool_error(
                    f"{restd.RESTD_NAME} was installed but is not in "
                    f"{restd.searched_places()}."
                )
            installed = True

        def work():
            # One daemon per server and metadata schema, whatever account
            # the connection uses.
            host = connection.get("host") or "localhost"
            server_port = connection.get("port") or 3306
            if sandbox_port is not None:
                directory = restd.sandbox_deployment_dir(
                    base_dir, sandbox_port, schema
                )
            else:
                directory = restd.connection_deployment_dir(
                    host, server_port, schema
                )
            if restd.is_deployment(directory):
                raise tool_error(
                    f"There is a REST Daemon deployment in '{directory}' "
                    "already. Delete it with rest.delete_restd_config first."
                )

            session = _open_session(connection, secret)
            try:
                if not exists:
                    statement = "CONFIGURE REST METADATA"
                    if schema != restd.DEFAULT_METADATA_SCHEMA:
                        statement += f" SCHEMA {db_functions.quote_identifier(schema)}"
                    session.run_sql(statement + " ENABLED")
                missing = _missing_privileges(_grants(session), schema)
            finally:
                session.close()
            if missing:
                raise tool_error(
                    f"The account {connection.get('user')} cannot set up a "
                    "REST Daemon. It lacks " + "; ".join(missing) + "."
                )

            daemon_port = port
            if daemon_port is None:
                daemon_port = restd.choose_port(restd.taken_ports(base_dir))
            elif not restd.is_port_free(daemon_port, restd.taken_ports(base_dir)):
                raise tool_error(f"Port {daemon_port} is in use.")

            # The one certificate every daemon here serves, made by the
            # first setup: trusted once, it is trusted for all of them.
            certificate = restd_certs.ensure()
            output = restd.setup(
                executable,
                directory,
                connection,
                secret,
                daemon_port,
                schema,
                certificate=certificate,
                record={
                    "connection_uri": uri,
                    "host": host,
                    "server_port": server_port,
                    "sandbox_port": sandbox_port,
                    "created": general.utc_timestamp(),
                },
            )
            report = restd.start(executable, directory)
            report["installed"] = installed
            report["setup_output"] = output
            return report

        return await anyio.to_thread.run_sync(work)
