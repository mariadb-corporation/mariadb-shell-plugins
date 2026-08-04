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

"""MCP tools for working with database connections.

These tools expose the MariaDB Shell connection facilities over MCP:
listing the configured connections, opening a connection, listing the schemas
available on it and the objects within a schema, describing a single object,
running SQL statements on it and closing it again.

Only the connections configured via ``mcp.setup`` can be opened. Their URIs are
listed from, and their passwords read back from, the shell secret store (see
:mod:`mcp_plugin.lib.config`).

Open sessions are cached in-process, keyed by a UUID that ``db.connect``
returns. That UUID identifies the connection for the ``db.execute_sql`` and
``db.close`` tools. Connections are opened with ``shell.open_session`` so they
are independent of the shell's global session.

When the server is served over HTTP it is reachable by more than one client and
outlives the client that opened a connection, so two safeguards apply there
(and only there - over stdio the server talks to a single client, its own
parent process, for its entire lifetime):

* A connection is bound to the IP address of the client that opened it. A
  request coming from any other address is answered as if the connection did
  not exist, so a connection cannot be taken over by guessing its UUID.
* A connection that has been unused for
  :data:`mcp_plugin.lib.general.SESSION_IDLE_TIMEOUT` seconds has its session
  closed by a background reaper, releasing the server-side connection. The
  connection itself stays valid: the next tool call that uses it transparently
  opens a new session, whereas ``db.close`` just drops it without reopening
  anything. As the session is a new one, anything that only lived in the old
  session - temporary tables, session variables, the current schema, an open
  transaction - is gone.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver uuid SCHEMATA datatype
# cSpell:ignore ISNULL IFNULL ARRAYAGG utf8mb3 kcu ORDINAL DTD

import json
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

import mysqlsh

from mcp_plugin.lib import config, general

# Maps a connection UUID to its _Connection.
_sessions = {}

# Guards the _sessions dict itself; each connection has a lock of its own.
_sessions_lock = threading.Lock()

# How often the reaper looks for connections that have been idle for too long.
_IDLE_CHECK_INTERVAL = 30

# The thread closing idle connections, started with the first connection opened
# over HTTP.
_idle_reaper = None

# Lists the schemas of a server, classified into system and user schemas.
_LIST_SCHEMAS_SQL = """
    SELECT SCHEMA_NAME as schema_name,
        CASE
            WHEN SCHEMA_NAME = 'mysql'
                OR SCHEMA_NAME = 'mysql_rest_service_metadata' THEN 'System Schema'
            WHEN SCHEMA_NAME = 'information_schema' THEN 'System Information Schema'
            ELSE 'User Schema'
        END AS schema_type,
        SCHEMA_COMMENT as schema_comment
    FROM INFORMATION_SCHEMA.SCHEMATA
    ORDER BY SCHEMA_TYPE, SCHEMA_NAME
"""

# Lists the objects of one type within a schema. Each query takes the schema
# name as its single ? parameter. The columns returned depend on the object
# type: tables and views carry a comment, sequences their value data type, the
# remaining types just a name.
#
# Sequences and views have their own TABLE_TYPE in INFORMATION_SCHEMA.TABLES,
# so they do not show up in the table listing; system-versioned tables do have
# to be included there explicitly, as they are typed SYSTEM VERSIONED rather
# than BASE TABLE.
_LIST_OBJECTS_SQL = {
    "table": """
        SELECT TABLE_NAME as name, TABLE_COMMENT as comment
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ?
            AND TABLE_TYPE IN ('BASE TABLE', 'SYSTEM VERSIONED')
        ORDER BY TABLE_NAME
    """,
    "view": """
        SELECT TABLE_NAME as name, TABLE_COMMENT as comment
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'VIEW'
        ORDER BY TABLE_NAME
    """,
    "function": """
        SELECT ROUTINE_NAME as name
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_SCHEMA = ? AND ROUTINE_TYPE = 'FUNCTION'
        ORDER BY ROUTINE_NAME
    """,
    "procedure": """
        SELECT ROUTINE_NAME as name
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_SCHEMA = ? AND ROUTINE_TYPE = 'PROCEDURE'
        ORDER BY ROUTINE_NAME
    """,
    "sequence": """
        SELECT SEQUENCE_NAME as name, DATA_TYPE as datatype
        FROM INFORMATION_SCHEMA.SEQUENCES
        WHERE SEQUENCE_SCHEMA = ?
        ORDER BY SEQUENCE_NAME
    """,
    "trigger": """
        SELECT TRIGGER_NAME as name
        FROM INFORMATION_SCHEMA.TRIGGERS
        WHERE TRIGGER_SCHEMA = ?
        ORDER BY TRIGGER_NAME
    """,
    "event": """
        SELECT EVENT_NAME as name
        FROM INFORMATION_SCHEMA.EVENTS
        WHERE EVENT_SCHEMA = ?
        ORDER BY EVENT_NAME
    """,
}

# Looks a single object up by schema and name, one query per object type. Takes
# the schema and object name as its two ? parameters and yields the object's
# comment, so it doubles as the existence check for db.get_object_details. Only
# triggers have no comment of their own in the INFORMATION_SCHEMA.
_OBJECT_BASIC_SQL = {
    "table": """
        SELECT TABLE_COMMENT as comment
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            AND TABLE_TYPE IN ('BASE TABLE', 'SYSTEM VERSIONED')
    """,
    "view": """
        SELECT TABLE_COMMENT as comment
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND TABLE_TYPE = 'VIEW'
    """,
    "function": """
        SELECT ROUTINE_COMMENT as comment
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_SCHEMA = ? AND ROUTINE_NAME = ?
            AND ROUTINE_TYPE = 'FUNCTION'
    """,
    "procedure": """
        SELECT ROUTINE_COMMENT as comment
        FROM INFORMATION_SCHEMA.ROUTINES
        WHERE ROUTINE_SCHEMA = ? AND ROUTINE_NAME = ?
            AND ROUTINE_TYPE = 'PROCEDURE'
    """,
    "sequence": """
        SELECT TABLE_COMMENT as comment
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND TABLE_TYPE = 'SEQUENCE'
    """,
    "trigger": """
        SELECT NULL as comment
        FROM INFORMATION_SCHEMA.TRIGGERS
        WHERE TRIGGER_SCHEMA = ? AND TRIGGER_NAME = ?
    """,
    "event": """
        SELECT EVENT_COMMENT as comment
        FROM INFORMATION_SCHEMA.EVENTS
        WHERE EVENT_SCHEMA = ? AND EVENT_NAME = ?
    """,
}

# The type specific details of the objects that are described by a single row
# rather than by columns or parameters. Takes the schema and object name as its
# two ? parameters.
_OBJECT_DETAILS_SQL = {
    "sequence": """
        SELECT DATA_TYPE AS data_type, START_VALUE AS start_value,
            INCREMENT AS increment
        FROM INFORMATION_SCHEMA.SEQUENCES
        WHERE SEQUENCE_SCHEMA = ? AND SEQUENCE_NAME = ?
    """,
    "trigger": """
        SELECT EVENT_MANIPULATION AS event_manipulation,
            ACTION_TIMING AS action_timing,
            ACTION_ORIENTATION AS action_orientation,
            ACTION_ORDER AS action_order,
            EVENT_OBJECT_SCHEMA AS table_schema,
            EVENT_OBJECT_TABLE AS table_name,
            ACTION_CONDITION AS action_condition,
            ACTION_STATEMENT AS action_statement,
            DEFINER AS definer,
            CREATED AS created,
            SQL_MODE AS sql_mode
        FROM INFORMATION_SCHEMA.TRIGGERS
        WHERE TRIGGER_SCHEMA = ? AND TRIGGER_NAME = ?
    """,
    "event": """
        SELECT EVENT_TYPE AS event_type,
            EVENT_DEFINITION AS event_definition,
            EXECUTE_AT AS execute_at,
            INTERVAL_VALUE AS interval_value,
            INTERVAL_FIELD AS interval_field,
            STARTS AS starts,
            ENDS AS ends,
            STATUS AS status,
            ON_COMPLETION AS on_completion,
            LAST_EXECUTED AS last_executed,
            DEFINER AS definer,
            TIME_ZONE AS time_zone
        FROM INFORMATION_SCHEMA.EVENTS
        WHERE EVENT_SCHEMA = ? AND EVENT_NAME = ?
    """,
}

# The parameters of a stored function or procedure, in ordinal position order.
# Takes the schema, routine name and upper case routine type as its three ?
# parameters. A function's RETURNS clause is the row at ordinal position 0, with
# no name and no mode, so it is filtered out of the parameters and reported
# separately.
_ROUTINE_PARAMETERS_SQL = """
    SELECT ORDINAL_POSITION as position, PARAMETER_NAME as name,
        PARAMETER_MODE as mode, DTD_IDENTIFIER as datatype,
        PARAMETER_DEFAULT as parameter_default
    FROM INFORMATION_SCHEMA.PARAMETERS
    WHERE SPECIFIC_SCHEMA = ? AND SPECIFIC_NAME = ? AND ROUTINE_TYPE = ?
    ORDER BY ORDINAL_POSITION
"""

# The columns of a table or view, in ordinal position order. Takes the schema
# and object name as its two ? parameters. The INFORMATION_SCHEMA columns are
# collated explicitly so comparing them against the parameters cannot fail with
# an illegal mix of collations.
_OBJECT_COLUMNS_SQL = """
    SELECT
        c.COLUMN_NAME AS name,
        c.COLUMN_TYPE AS datatype,
        c.IS_NULLABLE = 'NO' AS not_null,
        c.COLUMN_KEY = 'PRI' AS is_primary,
        c.COLUMN_KEY = 'UNI' AS is_unique,
        c.GENERATION_EXPRESSION <> '' AS is_generated,
        IF(c.EXTRA = 'auto_increment', 'auto_inc',
            IF(c.COLUMN_KEY = 'PRI' AND c.DATA_TYPE = 'binary'
                    AND c.CHARACTER_MAXIMUM_LENGTH = 16,
                'rev_uuid', NULL)) AS id_generation,
        c.COLUMN_COMMENT AS comment,
        c.COLUMN_DEFAULT AS column_default,
        c.CHARACTER_SET_NAME AS charset,
        c.COLLATION_NAME collation
    FROM INFORMATION_SCHEMA.COLUMNS AS c
    WHERE c.TABLE_SCHEMA COLLATE utf8mb3_general_ci = ?
        AND c.TABLE_NAME COLLATE utf8mb3_general_ci = ?
    ORDER BY c.ORDINAL_POSITION
"""

# The constraints of a table, one row per constrained column. Takes the schema
# and table name as its two ? parameters.
#
# The join has to match on TABLE_NAME as well: constraint names are unique per
# table, not per schema, so joining on the schema alone makes every table's
# PRIMARY constraint match every other table's primary key columns.
_OBJECT_CONSTRAINTS_SQL = """
    SELECT tc.CONSTRAINT_NAME as constraint_name,
        tc.CONSTRAINT_TYPE as constraint_type, kcu.COLUMN_NAME AS column_name
    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS tc
    LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS kcu
        ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
        AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
        AND tc.TABLE_NAME = kcu.TABLE_NAME
    WHERE tc.TABLE_SCHEMA = ? AND tc.TABLE_NAME = ?
    ORDER BY tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
"""

# The foreign key relationships of a table, in both directions. Takes the
# schema and table name TWICE as its four ? parameters, once for each half of
# the UNION: the first half are the references pointing from this table to
# other tables (n:1), the second half those pointing from other tables to this
# one (1:1 or 1:n, depending on whether the primary keys line up).
_OBJECT_REFERENCES_SQL = """
    SELECT MAX(c.ORDINAL_POSITION) + 100 AS position,
        MAX(k.REFERENCED_TABLE_NAME) AS name,
        GROUP_CONCAT(c.COLUMN_NAME SEPARATOR ', ') AS ref_column_names,
        JSON_MERGE_PRESERVE(
            JSON_OBJECT('kind', 'n:1'),
            JSON_OBJECT('constraint',
                CONCAT(MAX(k.CONSTRAINT_SCHEMA), '.', MAX(k.CONSTRAINT_NAME))),
            JSON_OBJECT('to_many', FALSE),
            JSON_OBJECT('referenced_schema', MAX(k.REFERENCED_TABLE_SCHEMA)),
            JSON_OBJECT('referenced_table', MAX(k.REFERENCED_TABLE_NAME)),
            JSON_OBJECT('column_mapping',
                JSON_ARRAYAGG(JSON_OBJECT(
                    'base', c.COLUMN_NAME,
                    'ref', k.REFERENCED_COLUMN_NAME)))
        ) AS reference_mapping,
        MAX(c.TABLE_SCHEMA) AS table_schema, MAX(c.TABLE_NAME) AS table_name
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS k
        JOIN INFORMATION_SCHEMA.COLUMNS AS c
            ON c.TABLE_SCHEMA = k.TABLE_SCHEMA AND c.TABLE_NAME = k.TABLE_NAME
                AND c.COLUMN_NAME=k.COLUMN_NAME
                AND c.TABLE_SCHEMA COLLATE utf8mb3_general_ci = ?
                AND c.TABLE_NAME COLLATE utf8mb3_general_ci = ?
    WHERE NOT ISNULL(k.REFERENCED_TABLE_NAME)
    GROUP BY k.CONSTRAINT_NAME, k.table_schema, k.table_name
    UNION
    -- Union with the references that point from other tables to the table
    -- (1:1 and 1:n)
    SELECT MAX(c.ORDINAL_POSITION) + 1000 AS position,
        MAX(c.TABLE_NAME) AS name,
        GROUP_CONCAT(k.COLUMN_NAME SEPARATOR ', ') AS ref_column_names,
        JSON_MERGE_PRESERVE(
            -- If the PKs of the table and the referred table are exactly the
            -- same, this is a 1:1 relationship, otherwise an 1:n
            JSON_OBJECT('kind', IF(JSON_CONTAINS(MAX(PK_TABLE.PK), MAX(PK_REF.PK)) = 1,
                '1:1', '1:n')),
            JSON_OBJECT('constraint',
                CONCAT(MAX(k.CONSTRAINT_SCHEMA), '.', MAX(k.CONSTRAINT_NAME))),
            JSON_OBJECT('to_many', JSON_CONTAINS(MAX(PK_TABLE.PK), MAX(PK_REF.PK)) = 0),
            JSON_OBJECT('referenced_schema', MAX(c.TABLE_SCHEMA)),
            JSON_OBJECT('referenced_table', MAX(c.TABLE_NAME)),
            JSON_OBJECT('column_mapping',
                JSON_ARRAYAGG(JSON_OBJECT(
                    'base', k.REFERENCED_COLUMN_NAME,
                    'ref', c.COLUMN_NAME)))
        ) AS reference_mapping,
        MAX(k.REFERENCED_TABLE_SCHEMA) AS table_schema,
        MAX(k.REFERENCED_TABLE_NAME) AS table_name
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS k
        JOIN INFORMATION_SCHEMA.COLUMNS AS c
            ON c.TABLE_SCHEMA = k.TABLE_SCHEMA AND c.TABLE_NAME = k.TABLE_NAME
                AND c.COLUMN_NAME=k.COLUMN_NAME
        -- The PK columns of the table, e.g. ['test_fk.product.id']
        JOIN (SELECT JSON_ARRAYAGG(CONCAT(c2.TABLE_SCHEMA, '.',
                    c2.TABLE_NAME, '.', c2.COLUMN_NAME)) AS PK,
                c2.TABLE_SCHEMA, c2.TABLE_NAME
                FROM INFORMATION_SCHEMA.COLUMNS AS c2
                WHERE c2.COLUMN_KEY = 'PRI'
                GROUP BY c2.COLUMN_KEY, c2.TABLE_SCHEMA, c2.TABLE_NAME) AS PK_TABLE
            ON PK_TABLE.TABLE_SCHEMA = k.REFERENCED_TABLE_SCHEMA
                AND PK_TABLE.TABLE_NAME = k.REFERENCED_TABLE_NAME
        -- The PK columns of the referenced table,
        -- e.g. ['test_fk.product_part.id', 'test_fk.product.id']
        JOIN (SELECT JSON_ARRAYAGG(PK2.PK_COL) AS PK, PK2.TABLE_SCHEMA, PK2.TABLE_NAME
            FROM (SELECT IFNULL(
                CONCAT(MAX(k1.REFERENCED_TABLE_SCHEMA), '.',
                    MAX(k1.REFERENCED_TABLE_NAME), '.',
                    MAX(k1.REFERENCED_COLUMN_NAME)),
                CONCAT(c1.TABLE_SCHEMA, '.', c1.TABLE_NAME, '.',
                    c1.COLUMN_NAME)) AS PK_COL,
                c1.TABLE_SCHEMA AS TABLE_SCHEMA, c1.TABLE_NAME AS TABLE_NAME
                FROM INFORMATION_SCHEMA.COLUMNS AS c1
                    JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS k1
                        ON k1.TABLE_SCHEMA = c1.TABLE_SCHEMA
                            AND k1.TABLE_NAME = c1.TABLE_NAME
                            AND k1.COLUMN_NAME = c1.COLUMN_NAME
                WHERE c1.COLUMN_KEY = 'PRI'
                GROUP BY c1.COLUMN_NAME, c1.TABLE_SCHEMA, c1.TABLE_NAME) AS PK2
                GROUP BY PK2.TABLE_SCHEMA, PK2.TABLE_NAME) AS PK_REF
            ON PK_REF.TABLE_SCHEMA = k.TABLE_SCHEMA
                AND PK_REF.TABLE_NAME = k.TABLE_NAME
    WHERE k.REFERENCED_TABLE_SCHEMA COLLATE utf8mb3_general_ci = ?
        AND k.REFERENCED_TABLE_NAME COLLATE utf8mb3_general_ci = ?
    GROUP BY k.CONSTRAINT_NAME, c.TABLE_SCHEMA, c.TABLE_NAME
    ORDER BY position
"""


class _Connection:
    """A database connection opened with ``db.connect``.

    Holds the open shell session together with what is needed to police its
    use: the URI it was opened from, so a session closed for being idle can be
    opened again, the address of the client that opened it, and the time it was
    last used.

    The lock is held for as long as a tool works with the session. That keeps
    the idle reaper from closing a session out from under a running statement,
    and serializes the tool calls that share a connection - a shell session
    cannot run two statements at once anyway.
    """

    def __init__(self, uri: str, client_address: Optional[str]):
        self.uri = uri
        self.client_address = client_address
        self.session = None
        self.last_used = time.monotonic()
        self.lock = threading.RLock()

    def is_accessible_from(self, client_address: Optional[str]) -> bool:
        """Returns whether a client at the given address may use this connection.

        Over stdio there is only ever one client, so the address is not
        checked. Over HTTP the connection belongs to the client that opened it
        and to no one else.

        Args:
            client_address (str): The address the request came from, or None.

        Returns:
            True if the connection may be used by that client.
        """
        if not general.is_http_transport():
            return True

        return (
            client_address is not None and client_address == self.client_address
        )

    def open_session(self):
        """Opens the session, or returns the one already open.

        Returns:
            The open shell session.
        """
        with self.lock:
            if self.session is None:
                self.session = _open_session(self.uri)

            return self.session

    def close_session(self) -> None:
        """Closes the session, if one is open.

        Returns:
            None
        """
        with self.lock:
            session = self.session
            self.session = None

        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - the connection may be gone
                pass

    def close_session_if_idle(self, timeout: float) -> bool:
        """Closes the session if it has not been used for ``timeout`` seconds.

        Never waits for the lock: a connection another thread is working with
        is by definition not idle.

        Args:
            timeout (float): The idle time after which to close, in seconds.

        Returns:
            True if a session was closed.
        """
        if not self.lock.acquire(blocking=False):
            return False

        try:
            if (
                self.session is None
                or time.monotonic() - self.last_used < timeout
            ):
                return False

            self.close_session()

            return True
        finally:
            self.lock.release()


def _open_session(uri: str):
    """Opens a shell session for one of the configured connection URIs.

    Args:
        uri (str): The connection URI, as configured via ``mcp.setup``.

    Returns:
        The open shell session.
    """
    # Read the stored password back and open the session with it. The session
    # is independent of the shell's global session.
    connection_data = mysqlsh.globals.shell.parse_uri(uri)
    connection_data["password"] = config.get_connection_password(uri)

    return mysqlsh.globals.shell.open_session(connection_data)


def _get_connection(connection_id: str, client_address: Optional[str]):
    """Looks a connection up, checking that the client may use it.

    A connection that exists but belongs to another client is reported exactly
    like one that does not exist, so the error cannot be used to tell valid
    connection ids from invalid ones.

    Args:
        connection_id (str): The UUID returned by ``db.connect``.
        client_address (str): The address the request came from, or None.

    Returns:
        The :class:`_Connection`.
    """
    with _sessions_lock:
        connection = _sessions.get(connection_id)

    if connection is None or not connection.is_accessible_from(client_address):
        raise mysqlsh.Error(
            f"No open connection found for id '{connection_id}'. "
            "Open one first with db.connect."
        )

    return connection


@contextmanager
def use_session(connection_id: str, client_address: Optional[str] = None):
    """Yields the session of a connection opened with ``db.connect``.

    The public entry point for the other tool modules, which work on the
    connections this one hands out but have no cache of their own. The session
    is opened again if it had been closed for being idle, and stays reserved
    for the caller for the duration of the ``with`` block.

    Args:
        connection_id (str): The UUID returned by ``db.connect``.
        client_address (str): The address the request came from, as returned by
            :func:`mcp_plugin.lib.general.get_client_address`. Required while
            serving over HTTP, where a connection may only be used by the
            client that opened it.

    Yields:
        The open shell session.
    """
    connection = _get_connection(connection_id, client_address)

    with connection.lock:
        session = connection.open_session()
        try:
            yield session
        finally:
            connection.last_used = time.monotonic()


def _close_idle_sessions() -> int:
    """Closes the sessions of all connections that have been idle too long.

    The connections themselves are kept, so they can be used again: the next
    tool call opens a new session for them.

    Returns:
        The number of sessions that were closed.
    """
    timeout = general.SESSION_IDLE_TIMEOUT
    with _sessions_lock:
        connections = list(_sessions.values())

    return sum(
        1
        for connection in connections
        if connection.close_session_if_idle(timeout)
    )


def _reap_idle_sessions() -> None:
    """Closes idle sessions for as long as the server runs.

    Returns:
        None
    """
    while True:
        time.sleep(_IDLE_CHECK_INTERVAL)
        try:
            _close_idle_sessions()
        except Exception:  # noqa: BLE001 - the reaper must never die
            pass


def _start_idle_reaper() -> None:
    """Starts the idle-session reaper, once, when serving over HTTP.

    Returns:
        None
    """
    global _idle_reaper

    if not general.is_http_transport():
        return

    with _sessions_lock:
        if _idle_reaper is not None:
            return

        # A daemon thread: it only ever closes idle sessions, so it must not
        # keep the process alive when the server stops serving.
        _idle_reaper = threading.Thread(
            target=_reap_idle_sessions,
            name="mcp-db-idle-session-reaper",
            daemon=True,
        )
        _idle_reaper.start()


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
                # Values that are not natively JSON-serializable have to be
                # converted, as the tool result is returned as JSON. The shell
                # hands the types that have no JSON equivalent of their own -
                # decimals, dates and times - back as text already, so only
                # binary data needs converting; anything else the shell might
                # return in a type of its own falls back to its text form
                # rather than failing to serialize.
                if isinstance(value, (bytes, bytearray)):
                    value = value.hex()
                elif value is not None and not isinstance(
                    value, (bool, int, float, str)
                ):
                    value = str(value)
                item[column] = value
            rows.append(item)
        output["columns"] = columns
        output["rows"] = rows

    return output


def _query_rows(session, sql: str, params: Optional[list] = None) -> list:
    """Runs a query and returns just its rows.

    Args:
        session: The open shell session to run the query on.
        sql: The query to run.
        params: The parameters to bind to the ? placeholders, in order.

    Returns:
        A list with one dict per row, empty when the query matched nothing.
    """
    result = session.run_sql(sql, params if params is not None else [])

    return _serialize_result(result).get("rows", [])


def _normalize_object_type(object_type: str) -> str:
    """Validates a database object type and normalizes it to lower case.

    Args:
        object_type (str): The object type to check, matched case-insensitively.

    Returns:
        The normalized object type.
    """
    normalized = object_type.strip().lower()
    if normalized not in _LIST_OBJECTS_SQL:
        raise mysqlsh.Error(
            f"'{object_type}' is not a supported object type. Supported "
            f"types are: {', '.join(_LIST_OBJECTS_SQL)}."
        )

    return normalized


def _parse_json_fields(rows: list, field: str) -> list:
    """Expands a JSON column returned as text into real Python values.

    Leaves the value untouched if it is not a JSON string, so the rows are
    usable whether the server hands the column back as text or the shell has
    already converted it.

    Args:
        rows (list): The rows to patch up, modified in place.
        field (str): The name of the JSON column.

    Returns:
        The same list of rows.
    """
    for row in rows:
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field] = json.loads(value)
            except ValueError:
                pass

    return rows


def register_db_tools(server, function_groups=()) -> None:
    """Registers the database connection tools on the given server.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served. Unused here,
            as none of the db tools depend on another group.

    Returns:
        None
    """
    from mcp.server.mcpserver import Context

    @server.tool(name="db.list_connections")
    def list_connections() -> list:
        """Lists the configured database connection URIs.

        Returns:
            The list of connection URIs configured via mcp.setup. Any of these
            can be passed to db.connect.
        """
        return config.list_connection_uris()

    @server.tool(name="db.connect")
    def connect(ctx: Context, uri: str) -> str:
        """Opens a configured database connection and caches it.

        The connection remains usable until it is closed with db.close. When
        the server is served over HTTP it can only be used by the client that
        opened it, and its database session is closed once it has been unused
        for a while. The next call using the connection opens a new session
        automatically, so no state that only lived in the previous session -
        temporary tables, session variables, the current schema or an open
        transaction - survives an idle period.

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

        # The connection belongs to the client that opens it. Without an
        # address to bind it to there is no way to keep another client from
        # using it, so serving over HTTP requires one.
        client_address = general.get_client_address(ctx)
        if general.is_http_transport() and client_address is None:
            raise mysqlsh.Error(
                "The address of the client could not be determined, so the "
                "connection cannot be bound to it. Connections can only be "
                "opened by a client the server can identify."
            )

        connection = _Connection(uri, client_address)
        # Opened right away, so a bad password or an unreachable server is
        # reported by db.connect rather than by the first tool using it.
        connection.open_session()

        connection_id = str(uuid.uuid4())
        with _sessions_lock:
            _sessions[connection_id] = connection

        _start_idle_reaper()

        return connection_id

    @server.tool(name="db.list_schemas")
    def list_schemas(ctx: Context, connection_id: str) -> list:
        """Lists the database schemas available on an open connection.

        Args:
            connection_id: The UUID returned by db.connect.

        Returns:
            A list with one dict per schema, holding its name (schema_name), its
            classification as a system or user schema (schema_type) and its
            comment (schema_comment). Only the schemas the connection's account
            has access to are listed.
        """
        with use_session(connection_id, general.get_client_address(ctx)) as session:
            return _query_rows(session, _LIST_SCHEMAS_SQL)

    @server.tool(name="db.list_objects")
    def list_objects(
        ctx: Context,
        connection_id: str,
        schema_name: str,
        object_type: str = "table",
    ) -> list:
        """Lists the objects of one type within a database schema.

        Args:
            connection_id: The UUID returned by db.connect.
            schema_name: The schema to list the objects of, as returned by
                db.list_schemas.
            object_type: The type of object to list: table, view, function,
                procedure, sequence, trigger or event. Defaults to table.

        Returns:
            A list with one dict per object, always holding its name (name).
            Tables and views additionally carry their comment (comment) and
            sequences their value data type (datatype). Only the objects the
            connection's account has access to are listed; an unknown schema
            yields an empty list.
        """
        sql = _LIST_OBJECTS_SQL[_normalize_object_type(object_type)]

        with use_session(connection_id, general.get_client_address(ctx)) as session:
            return _query_rows(session, sql, [schema_name])

    @server.tool(name="db.get_object_details")
    def get_object_details(
        ctx: Context,
        connection_id: str,
        schema_name: str,
        object_name: str,
        object_type: str = "table",
    ) -> dict:
        """Describes a database object in detail.

        Args:
            connection_id: The UUID returned by db.connect.
            schema_name: The schema holding the object, as returned by
                db.list_schemas.
            object_name: The name of the object, as returned by db.list_objects.
            object_type: The type of the object: table, view, function,
                procedure, sequence, trigger or event. Defaults to table.

        Returns:
            A dict describing the object. It always holds the object itself
            (basic: its schema, name, type and comment), plus what applies to
            its type:

            - a table adds its columns in ordinal position order (columns), its
              constraints, one entry per constrained column (constraints), and
              its foreign key relationships in both directions (references) -
              those pointing from this table to other tables (n:1) as well as
              those pointing from other tables to this one (1:1 or 1:n)
            - a view adds its columns (columns)
            - a function or procedure adds its parameters in ordinal position
              order, each with its name, mode, data type and default
              (parameters), and, for a function, the data type it returns
              (returns)
            - a sequence, trigger or event adds the properties of its type
              (details)
        """
        normalized = _normalize_object_type(object_type)
        object_id = [schema_name, object_name]

        with use_session(connection_id, general.get_client_address(ctx)) as session:
            basic = _query_rows(session, _OBJECT_BASIC_SQL[normalized], object_id)
            if not basic:
                raise mysqlsh.Error(
                    f"No {normalized} '{object_name}' found in schema "
                    f"'{schema_name}'. Use db.list_objects to list the "
                    f"{normalized}s of a schema."
                )

            details = {
                "basic": {
                    "schema": schema_name,
                    "name": object_name,
                    "type": normalized,
                    "comment": basic[0].get("comment"),
                }
            }

            if normalized in ("table", "view"):
                details["columns"] = _query_rows(
                    session, _OBJECT_COLUMNS_SQL, object_id
                )

            if normalized == "table":
                details["constraints"] = _query_rows(
                    session, _OBJECT_CONSTRAINTS_SQL, object_id
                )
                # The reference mapping is assembled as JSON by the server, so
                # it arrives as a string that has to be expanded to stay usable.
                details["references"] = _parse_json_fields(
                    _query_rows(
                        session, _OBJECT_REFERENCES_SQL, object_id + object_id
                    ),
                    "reference_mapping",
                )
            elif normalized in ("function", "procedure"):
                rows = _query_rows(
                    session,
                    _ROUTINE_PARAMETERS_SQL,
                    object_id + [normalized.upper()],
                )
                # Ordinal position 0 is the RETURNS clause of a function, the
                # parameters proper start at 1. A procedure returns nothing.
                returns = [row for row in rows if row["position"] == 0]
                details["parameters"] = [
                    {
                        key: row[key]
                        for key in (
                            "name",
                            "mode",
                            "datatype",
                            "parameter_default",
                        )
                    }
                    for row in rows
                    if row["position"] != 0
                ]
                details["returns"] = returns[0]["datatype"] if returns else None
            elif normalized in _OBJECT_DETAILS_SQL:
                rows = _query_rows(
                    session, _OBJECT_DETAILS_SQL[normalized], object_id
                )
                details["details"] = rows[0] if rows else {}

            return details

    @server.tool(name="db.execute_sql")
    def execute_sql(
        ctx: Context,
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
        with use_session(connection_id, general.get_client_address(ctx)) as session:
            result = session.run_sql(sql, params if params is not None else [])

            return _serialize_result(result)

    @server.tool(name="db.execute_sql_script")
    def execute_sql_script(
        ctx: Context,
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

        with use_session(connection_id, general.get_client_address(ctx)) as session:
            results = []
            for statement in mysqlsh.mysql.split_script(sql_script):
                if statement.strip() == "":
                    continue
                results.append(_serialize_result(session.run_sql(statement, [])))

            return results

    @server.tool(name="db.close")
    def close(ctx: Context, connection_id: str) -> None:
        """Closes an open database connection.

        Args:
            connection_id: The UUID returned by db.connect.

        Returns:
            None
        """
        connection = _get_connection(
            connection_id, general.get_client_address(ctx)
        )

        with _sessions_lock:
            _sessions.pop(connection_id, None)

        # Closes the session if one is open. A session that was already closed
        # for being idle is not opened again just to close it.
        connection.close_session()
