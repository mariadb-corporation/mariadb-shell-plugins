# Copyright (c) 2026, MariaDB plc and/or its affiliates.
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

"""MCP tools for working with database connections.

These tools expose the MariaDB/MySQL Shell connection facilities over MCP:
listing the configured connections, opening a connection, running SQL
statements on it and closing it again.

Only the connections configured via ``mcp.setup`` can be opened. Their URIs are
listed from, and their passwords read back from, the shell secret store (see
:mod:`mcp_plugin.lib.config`).

Open sessions are cached in-process, keyed by a UUID that ``db.connect``
returns. That UUID identifies the connection for the ``db.execute_sql`` and
``db.close`` tools. Connections are opened with ``shell.open_session`` so they
are independent of the shell's global session.
"""

# cSpell:ignore mysqlsh MariaDB fastmcp uuid

import uuid
from typing import Optional

import mysqlsh

from mcp_plugin.lib import config

# Maps a connection UUID to its open shell session.
_sessions = {}


def _get_session(connection_uri: str):
    """Returns the cached session for the given connection id.

    Args:
        connection_id (str): The UUID returned by ``db.connect``.

    Returns:
        The open shell session.
    """
    session = _sessions.get(connection_uri)
    if session is None:
        raise mysqlsh.Error(
            f"No open connection found for id '{connection_uri}'. "
            "Open one first with db.connect."
        )
    return session


def _serialize_result(result) -> dict:
    """Serializes a shell SQL result into a JSON-friendly dict.

    Args:
        result: The result object returned by ``session.run_sql``.

    Returns:
        A dict with the result set (columns and rows) and execution metadata.
    """
    output = {
        "affected_items_count": result.affected_items_count,
        "warnings_count": result.warnings_count,
    }

    if result.has_data():
        columns = [col.get_column_label() for col in result.get_columns()]
        rows = []
        for row in result.fetch_all():
            item = {}
            for column in columns:
                value = row.get_field(column)
                # Fall back to a string for values that are not natively
                # JSON-serializable (e.g. binary data).
                if isinstance(value, (bytes, bytearray)):
                    value = value.hex()
                item[column] = value
            rows.append(item)
        output["columns"] = columns
        output["rows"] = rows

    return output


def register_db_tools(server) -> None:
    """Registers the database connection tools on the given server.

    Args:
        server: The FastMCP server instance to register the tools on.

    Returns:
        None
    """

    @server.tool(name="db.list_connections")
    def list_connections() -> list:
        """Lists the configured database connection URIs.

        Returns:
            The list of connection URIs configured via mcp.setup. Any of these
            can be passed to db.connect.
        """
        return config.list_connection_uris()

    @server.tool(name="db.connect")
    def connect(uri: str) -> str:
        """Opens a configured database connection and caches it.

        Args:
            uri: A connection URI, as returned by db.list_connections.

        Returns:
            The UUID identifying the open connection. Pass it to
            db.execute_sql and db.close.
        """
        if uri not in config.list_connection_uris():
            raise mysqlsh.Error(
                f"'{uri}' is not a configured connection. Use db.list_connections "
                "to list the available connections, or configure it with mcp.setup."
            )

        # Read the stored password back and open the session with it.
        connection_data = mysqlsh.globals.shell.parse_uri(uri)
        connection_data["password"] = config.get_connection_password(uri)
        session = mysqlsh.globals.shell.open_session(connection_data)

        connection_id = str(uuid.uuid4())
        _sessions[connection_id] = session

        return connection_id

    @server.tool(name="db.execute_sql")
    def execute_sql(
        connection_id: str,
        sql: str,
        params: Optional[list] = None,
    ) -> dict:
        """Executes a SQL statement on an open connection.

        Args:
            connection_id: The UUID returned by db.connect.
            sql: The SQL statement to run. May contain ? placeholders.
            params: The parameters to bind to the ? placeholders, in order.

        Returns:
            A dict with the result set (columns and rows) and execution
            metadata.
        """
        session = _get_session(connection_id)
        result = session.run_sql(sql, params if params is not None else [])

        return _serialize_result(result)

    @server.tool(name="db.execute_sql_script")
    def execute_sql_script(
        connection_id: str,
        sql_script: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> list:
        """Executes a multi-statement SQL script on an open connection.

        The script is split into individual statements which are executed in
        order, so several semicolon-separated statements can be run in a single
        call. Use db.execute_sql for a single parameterized statement.

        The script can be provided either inline via sql_script or read from a
        file on disk via file_path. Exactly one of the two must be given. A
        file_path must be within one of the directories allowed via mcp.setup.

        Args:
            connection_id: The UUID returned by db.connect.
            sql_script: One or more semicolon-separated SQL statements.
            file_path: Path to a .sql file on disk to read the script from,
                as an alternative to sql_script.

        Returns:
            A list with one entry per executed statement, each a dict with the
            result set (columns and rows) and execution metadata.
        """
        if (sql_script is None) == (file_path is None):
            raise mysqlsh.Error(
                "Provide exactly one of 'sql_script' or 'file_path'."
            )

        if file_path is not None:
            if not config.is_path_allowed(file_path):
                raise mysqlsh.Error(
                    f"Access to path '{file_path}' is not allowed. Add it (or a "
                    "parent directory) to the allowed paths with mcp.setup."
                )
            with open(file_path, "r", encoding="utf-8") as script_file:
                sql_script = script_file.read()

        session = _get_session(connection_id)

        results = []
        for statement in mysqlsh.mysql.split_script(sql_script):
            if statement.strip() == "":
                continue
            results.append(_serialize_result(session.run_sql(statement, [])))

        return results

    @server.tool(name="db.close")
    def close(connection_id: str) -> None:
        """Closes an open database connection.

        Args:
            connection_id: The UUID returned by db.connect.

        Returns:
            None
        """
        session = _get_session(connection_id)
        session.close()
        _sessions.pop(connection_id, None)
