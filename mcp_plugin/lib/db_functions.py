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
:mod:`mcp_plugin.lib.config`). The URI ``db.connect`` is called with is resolved
to the spelling it is configured under, so a client is not held to the exact one
- it only has to name the same connection (see
:func:`mcp_plugin.lib.config.normalize_connection_uri`), which among other
things means a URI with no scheme names the ``mariadb://`` one.

Open sessions are cached in-process, keyed by a UUID that ``db.connect``
returns. That UUID identifies the connection for the ``db.execute_sql`` and
``db.close`` tools. Connections are opened with ``shell.open_session`` so they
are independent of the shell's global session.

When the server is served over HTTP it is reachable by more than one client and
outlives the client that opened a connection, which is what the safeguards below
are there for. Over stdio the server talks to a single client, its own parent
process, for its entire lifetime, so the first two have little to do there - but
the third holds in both, and says so:

* A connection is bound to the client that opened it - both its peer address
  and its MCP session id (see
  :class:`mcp_plugin.lib.general.ClientIdentity`). Any other client is answered
  as if the connection did not exist, so a connection cannot be taken over by
  guessing its UUID. The session id is the part that carries the weight: it is
  a server-generated secret, whereas an address is shared by every client
  behind one NAT or reverse proxy and by every process on the machine on the
  default loopback bind. The check is a plain comparison of the two identities,
  not something switched on by the transport: over stdio a request has neither
  part, so the one client always matches itself, while over HTTP
  ``db.connect`` refuses to open a connection it cannot fully identify the
  client of.

  Note that this is not authentication: it binds a connection to whoever opened
  it, but any client that can reach the port can open one of its own on the
  stored credentials. See the README.
* A connection that has been unused for
  :data:`mcp_plugin.lib.general.SESSION_IDLE_TIMEOUT` seconds has its session
  closed by a background reaper, releasing the server-side connection. The
  connection itself stays valid: the next tool call that uses it transparently
  opens a new session, whereas ``db.close`` just drops it without reopening
  anything. As the session is a new one, anything that only lived in the old
  session - temporary tables, session variables, the current schema, an open
  transaction - is gone. The call that opens it says so: its result carries
  ``session_restarted: true``, because a client cannot otherwise tell, and the
  case that matters is silent - a ``COMMIT`` arriving on a session that never saw
  the ``START TRANSACTION`` succeeds and commits nothing at all.
* A connection stops existing altogether once it is
  :data:`mcp_plugin.lib.general.CONNECTION_MAX_LIFETIME` seconds old, however
  much it has been used in between. Recycling the session alone would leave the
  UUID valid for as long as the server runs: worth guessing indefinitely, and
  worth using indefinitely even after the connection it was opened on had been
  taken away. This one is applied when a connection is used, so it holds over
  stdio too, and by the reaper, so it also reaches the connections nobody comes
  back to. Both an expired connection and one that never existed are reported
  the same way, and the client's move is the same in either case: call
  ``db.connect`` again.

* There are only so many connections to be had:
  :data:`mcp_plugin.lib.general.MAX_CONNECTIONS_PER_CLIENT` for one client and
  :data:`mcp_plugin.lib.general.MAX_CONNECTIONS_TOTAL` altogether. Opening one
  costs a client one tool call and the server a database session, so without a
  limit a loop of ``db.connect`` calls would take the server's connections and
  this process's memory with it. Room is claimed before the session is opened,
  and under the lock that counts it, so a refused call costs the database
  nothing and a burst of concurrent calls cannot collectively overshoot.

A session can also die without anybody closing it: the server is restarted, the
connection is KILLed, or something between the two has an idle timeout shorter
than ours. The shell cannot tell - its ``is_open()`` reports whether a connection
handle exists on this side, which stays true for a connection the server has long
since dropped - so the failure has to be recognized from the error a statement
raises (see :data:`_CONNECTION_LOST_ERRORS`). The session is then discarded and
the next call opens a new one, which is what keeps a connection from failing every
call until it is closed.

Closing a connection - with ``db.close`` or by its expiring - is final, and that
takes a flag rather than a lock. A tool call resolves its connection out of the
cache and only then locks it, so a close can run entirely in between; without the
flag the call would find no session, open a fresh one on a record that is no
longer in the cache, and leave a server-side connection nothing can ever close
again. Tool calls do run concurrently: the SDK runs the sync db tools in worker
threads, and ``msm.deploy_schema`` awaits before working with its session.

Removing a connection with ``mcp.setup``, or deleting the sandbox that
registered one with ``sandbox.delete``, therefore does revoke it: the URI is
checked again every time a session is opened - a first open and a reopen alike -
and no connection outlives its maximum lifetime.

What these safeguards do is written to stderr as it happens (see
:func:`mcp_plugin.lib.general.log_event`): a connection opened and the client it
was bound to, a use refused, a session closed for being idle, a connection
dropped, and any failure while closing one or during a reaper pass. The refusals
are the reason this
exists rather than being left to whoever reads the tool errors: a client using
somebody else's connection is answered as if that connection did not exist, so
the log is the only place the attempt can be seen at all.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver uuid SCHEMATA datatype
# cSpell:ignore ISNULL IFNULL ARRAYAGG utf8mb3 kcu ORDINAL DTD

import asyncio
import json
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

import mysqlsh

from mcp_plugin.lib import config, general
from mcp_plugin.lib.tool_registrar import tool_registrar

# Maps a connection UUID to its _Connection.
_sessions = {}

# Guards the _sessions dict itself; each connection has a lock of its own.
_sessions_lock = threading.Lock()

# How often the reaper looks for connections to drop or sessions to close.
_REAP_INTERVAL = 30

# The thread dropping expired connections and closing idle sessions. It belongs
# to the server: lib/server.start() starts it before serving over HTTP and stops
# it when serving ends. _reaper_lock guards the handle - deliberately not the
# cache lock, which the thread itself needs - and _reaper_stop is how it is asked
# to finish without waiting out the interval it is in.
_reaper = None
_reaper_lock = threading.Lock()
_reaper_stop = threading.Event()

# The client-side error codes that mean the connection to the server is gone
# rather than that a statement was wrong: CR_SERVER_GONE_ERROR,
# CR_SERVER_LOST and CR_SERVER_LOST_EXTENDED. A session that reports one of
# these is finished - nothing else can be run on it - so it is thrown away and
# the next call opens a new one (see use_session).
#
# Deliberately a short list of exactly the codes that mean this, not "any client
# error": throwing a working session away costs a reconnect, and a lost
# connection that first surfaces under some other code is caught on the next
# call anyway, which always reports CR_SERVER_GONE_ERROR.
_CONNECTION_LOST_ERRORS = (2006, 2013, 2055)

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
#
# The aggregates that build ref_column_names and column_mapping are ordered by
# k.ORDINAL_POSITION - the column's place in the FOREIGN KEY, not in the table.
# Without an ORDER BY the sequence of a composite key's columns is whatever the
# plan produces, and the key's order is the one that means something: for
# FOREIGN KEY (b, a) REFERENCES parent (x, y), the table's order reports
# "a, b" with the mapping a->y, b->x, so a reader reconstructing the key from
# that sequence gets it back inside out. Each pair is correct either way, being
# one row.
#
# The two JSON_ARRAYAGGs in the PK subqueries below are deliberately left
# unordered: they are only ever compared with JSON_CONTAINS, which ignores array
# order (verified), so ordering them would buy nothing.
_OBJECT_REFERENCES_SQL = """
    SELECT MAX(c.ORDINAL_POSITION) + 100 AS position,
        MAX(k.REFERENCED_TABLE_NAME) AS name,
        GROUP_CONCAT(c.COLUMN_NAME ORDER BY k.ORDINAL_POSITION
            SEPARATOR ', ') AS ref_column_names,
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
                    'ref', k.REFERENCED_COLUMN_NAME)
                    ORDER BY k.ORDINAL_POSITION))
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
        GROUP_CONCAT(k.COLUMN_NAME ORDER BY k.ORDINAL_POSITION
            SEPARATOR ', ') AS ref_column_names,
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
                    'ref', c.COLUMN_NAME)
                    ORDER BY k.ORDINAL_POSITION))
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


class _ConnectionClosed(Exception):
    """Raised when a connection is asked for a session after it was closed.

    Internal to this module. :func:`use_session` turns it into the error an
    unknown connection id gets, which is what a closed connection is by then -
    the caller lost a race with ``db.close`` or with the connection expiring, and
    the answer is the same as if the id had never been handed out.
    """


class _Connection:
    """A database connection opened with ``db.connect``.

    Holds the open shell session together with what is needed to police its
    use: the URI it was opened from and the connection list it came out of, so
    a session closed for being idle can be opened again, the address of the
    client that opened it, the time it was last used and the time it was opened
    - the first bounding the life of its session, the second the life of the
    connection itself.

    The lock is held for as long as a tool works with the session. That keeps
    the idle reaper from closing a session out from under a running statement,
    and serializes the tool calls that share a connection - a shell session
    cannot run two statements at once anyway.
    """

    def __init__(self, uri: str, client, kind=None):
        self.uri = uri
        # Which of the two connection lists the URI was resolved in (see
        # mcp_plugin.lib.config). Kept for the life of the connection because
        # the URI alone no longer identifies one: the password is read under
        # this kind, and so is the re-validation every reopened session does.
        self.kind = config.normalize_connection_kind(kind)
        # Normalized on the way in, so the stored identity and the one a later
        # request is compared against are always in the same form.
        self.client = general.normalize_client_identity(client)
        self.session = None
        self.opened_at = time.monotonic()
        self.last_used = self.opened_at
        # Set once the connection is over, and never unset. Not the same as
        # having no session: a session closed for being idle is opened again,
        # while a closed connection is never usable again. See close().
        self.closed = False
        # True only while a tool call that had to open a new session is running,
        # so that the call can report it. Belongs to the CALL, not to the
        # connection: use_session assigns it on the way in and clears it on the
        # way out, under the lock, so a later call cannot read it and report a
        # restart of its own that never happened.
        self.session_restarted = False
        self.lock = threading.RLock()

    def is_accessible_from(self, client) -> bool:
        """Returns whether the given client may use this connection.

        A connection may be used by the client that opened it and by no other:
        same peer address, same MCP session. That is one equality of the whole
        :class:`mcp_plugin.lib.general.ClientIdentity`, deliberately NOT
        conditional on the transport in use. Reading the active transport here
        would make the check fail open in every setting that does not go through
        :func:`mcp_plugin.lib.server.start`: :func:`build_mcp_server` is public,
        so an embedder can serve the tools itself, and the transport global is
        never reset when a server stops. The safeguard would then be silently
        off with nothing reporting it.

        The two parts answer different attacks. The address alone is weak: it
        is not a secret, it is shared by every client behind one NAT or reverse
        proxy, and it is shared by every process on the machine when the server
        is bound to loopback as it is by default. The MCP session id is a
        server-generated secret the client must have been told, so it is what
        actually keeps one client off another's connection - including when the
        two are indistinguishable by address.

        The equality covers stdio without needing to know it is stdio. There, a
        request has neither a peer address nor a session id, so a connection is
        opened with an empty identity and every later request presents an empty
        identity too - the single client always matches itself. And the two
        modes cannot bleed into each other: over HTTP ``db.connect`` refuses to
        open a connection it cannot fully identify the client of, so a request
        carrying an identity never meets a connection opened without one.

        Args:
            client (ClientIdentity): The identity of the requesting client, as
                returned by :func:`mcp_plugin.lib.general.get_client_identity`.

        Returns:
            True if the connection may be used by that client.
        """
        return general.normalize_client_identity(client) == self.client

    def has_expired(self, max_lifetime: float) -> bool:
        """Returns whether this connection has reached the end of its life.

        Counted from when the connection was opened and not affected by use:
        that is the point of it. An idle connection has its session recycled and
        goes on living, so without a limit that use cannot postpone, a UUID
        handed out once would stay worth guessing, and worth using, for as long
        as the server runs - including after the connection it was opened on was
        removed with ``mcp.setup``.

        Args:
            max_lifetime (float): The lifetime after which a connection is over,
                in seconds.

        Returns:
            True if the connection may no longer be used.
        """
        return time.monotonic() - self.opened_at >= max_lifetime

    def open_session(self):
        """Opens the session, or returns the one already open.

        The closed flag is checked here, under the lock, and that is what makes
        closing a connection final. A caller resolves a connection out of the
        cache and only then takes its lock, so a ``db.close`` (or an expiry drop)
        can run entirely in between: it takes the record out of the cache and
        closes the session, and this method would otherwise find no session and
        open a fresh one - on a record nothing can reach any more, so nothing
        would ever close it again, while the caller went on working on a
        connection its client had been told was closed.

        Raises:
            _ConnectionClosed: If the connection has been closed. Holding the
                lock across the close would not be enough on its own; the racing
                caller would simply open its new session afterwards.

        Returns:
            The open shell session.
        """
        with self.lock:
            if self.closed:
                raise _ConnectionClosed()

            if self.session is None:
                self.session = _open_session(self.uri, self.kind)

            return self.session

    def close(self) -> None:
        """Ends the connection for good, closing its session.

        The flag goes up first and under the lock, so that from this moment on
        nothing can open a session on this connection - including a call that
        already holds the record and is waiting for the lock. The session is then
        closed outside the lock, as closing it is a round trip to the server and
        nobody has anything to wait for: every use of this connection from here
        on is refused.

        Returns:
            None
        """
        with self.lock:
            self.closed = True

        self.close_session()

    def close_session(self) -> None:
        """Closes the session, if one is open.

        Leaves the connection itself usable, unless :meth:`close` has ended it:
        this is also how a session that has merely been idle for too long is
        recycled.

        Returns:
            None
        """
        with self.lock:
            session = self.session
            self.session = None

        if session is not None:
            try:
                session.close()
            except Exception as error:  # noqa: BLE001 - connection may be gone
                # The session is dropped either way - it is of no use to anyone
                # once it is out of the connection - but a server-side session
                # that could not be closed is worth knowing about rather than
                # swallowing.
                general.log_event(
                    f"db: closing the session of '{self.uri}' failed, dropping "
                    f"it anyway: {error}"
                )

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


def _open_session(uri: str, kind=None):
    """Opens a shell session for one of the configured connection URIs.

    The URI is checked against the configured connections here, on every open
    and not only in ``db.connect``, because a connection can be taken away while
    a UUID for it is still live: an operator can remove it with ``mcp.setup``,
    the extension can remove it with ``db.delete_connection``, and
    ``sandbox.delete`` removes the one its ``sandbox.deploy`` registered.
    Without this check, a session reopened after an idle period would come back
    on a URI that is no longer configured - the stored password is read again on
    every open, so the connection would keep working and removing it would not
    revoke anything.

    The check is made in the connection list the URI was resolved in, and in
    that one only: a connection deleted from the GUI list is revoked even where
    the MCP list happens to name the same server, which it would not be if any
    list holding the URI counted.

    Args:
        uri (str): The connection URI, as configured via ``mcp.setup`` or
            ``db.add_connection``.
        kind: The connection list it belongs to, or None for the MCP list (see
            :mod:`mcp_plugin.lib.config`).

    Returns:
        The open shell session.
    """
    kind = config.normalize_connection_kind(kind)

    # The list is read from the secret store on each call, so it reflects what
    # is configured now rather than what was configured when db.connect ran. The
    # stored spellings, because this URI is one: it came from find_connection,
    # and it is the key the password is about to be read under.
    if uri not in config.list_stored_connection_uris(kind):
        raise mysqlsh.Error(
            f"'{uri}' is no longer a configured connection. Use "
            "db.list_connections to see the configured connections and "
            "db.connect to open one of them."
        )

    # Read the stored password back and open the session with it. The session
    # is independent of the shell's global session.
    connection_data = mysqlsh.globals.shell.parse_uri(uri)
    connection_data["password"] = config.get_connection_password(uri, kind)

    return mysqlsh.globals.shell.open_session(connection_data)


def _is_connection_lost(error) -> bool:
    """Returns whether an error means the session's server connection is gone.

    Told apart from an ordinary SQL error by the error code, which is the only
    thing that distinguishes them: both arrive as a ``mysqlsh.DBError``, one
    carrying a client-side code from :data:`_CONNECTION_LOST_ERRORS` and the
    other a server-side one. Anything without a code at all - an error raised on
    our own side of the call - is not one of these.

    Args:
        error (BaseException): The error a tool call raised.

    Returns:
        True if the session it was raised on cannot be used again.
    """
    return getattr(error, "code", None) in _CONNECTION_LOST_ERRORS


def _no_such_connection(connection_id: str) -> mysqlsh.Error:
    """Returns the error a client gets for a connection it may not use.

    The same error for both cases - an id that was never handed out, and one
    that belongs to another client - built in one place so the two answers
    cannot drift apart. A client able to tell them apart could use the
    difference to find out which UUIDs are live connections.

    Args:
        connection_id (str): The UUID the client named.

    Returns:
        The error to raise.
    """
    return mysqlsh.Error(
        f"No open connection found for id '{connection_id}'. "
        "Open one first with db.connect."
    )


def _drop_connection(connection_id: str, reason: str) -> None:
    """Ends a connection for good: out of the cache, session closed.

    The lock order is the one used everywhere else - the cache lock is taken and
    released before the connection's own lock is - so this can be called from
    the reaper thread and from a tool call alike.

    Args:
        connection_id (str): The UUID of the connection to drop.
        reason (str): What is being recorded as the reason, for the log.

    Returns:
        None
    """
    with _sessions_lock:
        connection = _sessions.pop(connection_id, None)

    if connection is None:
        return

    general.log_event(
        f"db: dropped connection {general.log_id_prefix(connection_id)} "
        f"({general.describe_client(connection.client)}) - {reason}"
    )

    # Waits for a tool call that is still running on the session, unlike the
    # idle pass: the connection is already out of the cache, so nothing new can
    # reach it and there is nothing to be gained by leaving its session open.
    # close() rather than close_session(): this connection is over, and a call
    # that resolved it just before it left the cache must be refused rather than
    # served with a new session nothing would ever close.
    connection.close()


def _claim_connection_slot(connection_id: str, connection) -> None:
    """Puts a new connection into the cache, if there is room for one.

    The counting and the insertion happen under one hold of the cache lock, and
    before the session behind the connection is opened. Both matter: a check made
    and then released would let a burst of concurrent ``db.connect`` calls each
    see room and collectively overshoot the limits, and a slot claimed only after
    the session was opened would mean the server had already paid for a
    connection it is about to refuse.

    Connections that have reached their maximum lifetime are not counted. They
    are on their way out - the reaper drops them, and so does using one - so they
    must not keep a client out of a slot they no longer hold.

    Args:
        connection_id (str): The UUID the new connection is to be known by.
        connection (_Connection): The connection to make room for.

    Returns:
        None
    """
    total_limit = general.MAX_CONNECTIONS_TOTAL
    client_limit = general.MAX_CONNECTIONS_PER_CLIENT
    lifetime = general.CONNECTION_MAX_LIFETIME

    with _sessions_lock:
        total = 0
        for_client = 0
        for existing in _sessions.values():
            if existing.has_expired(lifetime):
                continue

            total += 1
            if existing.client == connection.client:
                for_client += 1

        if for_client < client_limit and total < total_limit:
            _sessions[connection_id] = connection

            return

    # Refused, which is logged, so it is done with the lock released.
    if for_client >= client_limit:
        general.log_event(
            f"db.connect: REFUSED - {general.describe_client(connection.client)} "
            f"already holds the maximum of {client_limit} open connections"
        )

        raise mysqlsh.Error(
            f"There are already {for_client} open connections for this client, "
            f"which is the maximum of {client_limit}. Close the ones that are "
            "no longer needed with db.close before opening another."
        )

    general.log_event(
        f"db.connect: REFUSED - the server holds the maximum of {total_limit} "
        f"open connections ({general.describe_client(connection.client)} asked "
        "for another)"
    )

    raise mysqlsh.Error(
        f"The server already has {total} open database connections, which is "
        f"the maximum of {total_limit}. Close the ones that are no longer "
        "needed with db.close, or try again later."
    )


def _get_connection(connection_id: str, client):
    """Looks a connection up, checking that it is still valid and whose it is.

    A connection that exists but belongs to another client is reported exactly
    like one that does not exist, so the error cannot be used to tell valid
    connection ids from invalid ones. Because of that, the refusal is recorded
    on the server: the client is told nothing, so the log is the only place an
    attempt to use somebody else's connection is visible.

    A connection past :data:`mcp_plugin.lib.general.CONNECTION_MAX_LIFETIME` is
    dropped here and reported as not existing, which is exactly what it is from
    that moment on. Enforcing it here rather than only in the reaper is what
    makes the lifetime hold in every mode the tools can be served in: the reaper
    only runs over HTTP, while ``sandbox.delete`` can revoke a connection over
    stdio just as well.

    Args:
        connection_id (str): The UUID returned by ``db.connect``.
        client (ClientIdentity): The identity of the requesting client.

    Returns:
        The :class:`_Connection`.
    """
    with _sessions_lock:
        connection = _sessions.get(connection_id)

    if connection is not None and connection.has_expired(
        general.CONNECTION_MAX_LIFETIME
    ):
        _drop_connection(
            connection_id,
            f"reached its maximum lifetime of "
            f"{general.CONNECTION_MAX_LIFETIME:g}s",
        )
        connection = None

    if connection is None:
        raise _no_such_connection(connection_id)

    if not connection.is_accessible_from(client):
        general.log_event(
            "db: REFUSED use of connection "
            f"{general.log_id_prefix(connection_id)} bound to "
            f"{general.describe_client(connection.client)} by a request from "
            f"{general.describe_client(client)}"
        )

        raise _no_such_connection(connection_id)

    return connection


def _refuse_on_the_event_loop() -> None:
    """Refuses to hand out a session on the thread that runs the event loop.

    Working with a connection means holding its lock for as long as the work
    takes, and that lock is a plain thread lock. On a worker thread - which is
    where the SDK runs every synchronous tool body - waiting for it costs the
    caller its own turn and nobody else anything. On the thread driving the
    event loop it costs everybody: the whole server stops answering, on every
    transport, until the statement holding the lock finishes. Measured, not
    supposed: a coroutine blocking on a lock a worker thread held for 0.6s let
    zero other tasks run in that time.

    So async tool code must hand session work to a worker thread
    (``anyio.to_thread.run_sync``) rather than do it inline, and this makes that
    a rule instead of a comment - the failure it prevents is invisible until a
    statement happens to be slow and a second call happens to want the same
    connection.

    The check asks whether an asyncio loop is running on THIS thread, which is
    what the MCP server and uvicorn run on. It would not recognize a trio
    backend; nothing here uses one.

    Returns:
        None
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop on this thread: a worker thread, or an ordinary synchronous
        # caller such as the tests. Both are exactly right.
        return

    raise RuntimeError(
        "A database session was requested on the thread running the MCP "
        "server's event loop. Session work blocks, and blocking here stops "
        "the server answering every other client, so async tool code has to "
        "run it on a worker thread with anyio.to_thread.run_sync."
    )


@contextmanager
def use_session(connection_id: str, client=None):
    """Yields the session of a connection opened with ``db.connect``.

    The public entry point for the other tool modules, which work on the
    connections this one hands out but have no cache of their own. The session
    is opened again if it had been closed for being idle - which re-checks that
    its URI is still a configured connection - and stays reserved for the caller
    for the duration of the ``with`` block. A connection past its maximum
    lifetime is reported as not existing, whoever asks for it.

    This is also where a session that has died is noticed: a statement failing
    because the connection to the server is gone throws the session away, so the
    connection recovers on the next call rather than failing on the same dead
    session until it is closed.

    Two rules for callers, both about the connection's lock, which is held for
    the whole ``with`` block:

    * **Never enter it on the event-loop thread.** Async tool code has to hand
      the block to a worker thread; entering it here is refused outright (see
      :func:`_refuse_on_the_event_loop`).
    * **Never await inside the block.** The lock belongs to a THREAD, not to a
      task, so it would not stop a second coroutine on that same thread - it
      would let it straight in (measured: an RLock held across an await is
      re-acquired by the next coroutine, reentrantly). Two tool calls would then
      work with one shell session at the same time, which is the one thing the
      lock exists to prevent. Anything that has to await - an elicitation, say -
      belongs before the block, as ``msm.deploy_schema`` does with its path
      checks.

    Args:
        connection_id (str): The UUID returned by ``db.connect``.
        client (ClientIdentity): The identity of the requesting client, as
            returned by :func:`mcp_plugin.lib.general.get_client_identity`. It
            must be the identity the connection was opened with, or the
            connection is reported as not existing. Over stdio that is empty on
            both sides; over HTTP a connection is never opened without a full
            identity, so an empty one never reaches a connection there.

    Yields:
        The open shell session.
    """
    _refuse_on_the_event_loop()

    connection = _get_connection(connection_id, client)

    with connection.lock:
        # Whether THIS call is the one that has to open a new session, which is
        # what the caller needs to be told: everything that only lived in the
        # previous session is gone, and a COMMIT arriving on the new one would
        # otherwise report success having committed nothing.
        restarted = connection.session is None

        try:
            session = connection.open_session()
        except _ConnectionClosed:
            # Closed between being looked up and being locked: by then it is a
            # connection that does not exist, and it is answered as one.
            raise _no_such_connection(connection_id) from None

        connection.session_restarted = restarted

        try:
            yield session
        except Exception as error:  # noqa: BLE001 - re-raised below
            if _is_connection_lost(error):
                # The session is finished: a restarted server, a KILL, or a
                # network device with an idle timeout shorter than ours has
                # taken the connection away, and the shell has no way of
                # noticing by itself - its is_open() only reports whether a
                # handle exists locally, and that stays true for a dead
                # connection. Throwing it away here is what keeps the
                # connection usable: the next call opens a new session, instead
                # of every call until db.close failing on the same corpse.
                #
                # The failed call is NOT retried. It may have run in part, and
                # anything it had open - a transaction above all - is gone with
                # the connection, so the client is told and decides.
                general.log_event(
                    "db: the server connection of "
                    f"{general.log_id_prefix(connection_id)} was lost "
                    f"({getattr(error, 'code', '?')}); its session is "
                    "discarded and the next call opens a new one"
                )
                connection.close_session()

            raise
        finally:
            # Cleared here, and assigned (not just set) on the way in: either
            # alone would keep the next CALL from reading a restart of its own,
            # and clearing it is what also keeps a reader outside a call - a
            # future one; there is none today - from finding a stale True.
            connection.session_restarted = False
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
        connections = list(_sessions.items())

    closed = 0
    for connection_id, connection in connections:
        if not connection.close_session_if_idle(timeout):
            continue

        closed += 1
        # Recorded because the connection outlives its session: a later tool
        # call on it runs on a new server session, and this line is what says
        # when the old one went away.
        general.log_event(
            "db: closed the idle session of connection "
            f"{general.log_id_prefix(connection_id)} "
            f"({general.describe_client(connection.client)}) after "
            f"{timeout:g}s unused; the connection stays valid and opens a new "
            "session when it is used again"
        )

    return closed


def _drop_expired_connections() -> int:
    """Drops every connection that has reached its maximum lifetime.

    The counterpart of the check made when a connection is used: this one
    reaches the connections nobody comes back to, so an abandoned one does not
    hold its record - and, until the idle pass gets to it, a server-side
    connection - for the rest of the server's life.

    Returns:
        The number of connections that were dropped.
    """
    lifetime = general.CONNECTION_MAX_LIFETIME
    with _sessions_lock:
        expired = [
            connection_id
            for connection_id, connection in _sessions.items()
            if connection.has_expired(lifetime)
        ]

    for connection_id in expired:
        _drop_connection(
            connection_id, f"reached its maximum lifetime of {lifetime:g}s"
        )

    return len(expired)


def _reap_connections() -> None:
    """Ends what has outlived its welcome, for as long as the server runs.

    Two limits, on two different things: a connection past its maximum lifetime
    is dropped altogether, and one whose session has merely been idle for too
    long keeps its place and loses its session.

    Closing a session from this thread is safe, and not the special case it looks
    like. The MCP SDK runs a sync tool body with ``anyio.to_thread.run_sync``, so
    a session is opened on an anyio worker thread and used from whichever worker
    thread the next call lands on - the event-loop thread never touches it. What
    makes it safe rather than merely usual is that the MariaDB Connector/C the
    shell links has ``mysql_thread_init()`` and ``mysql_thread_end()`` as empty
    functions, so there is no per-thread client state to be missing, and
    ``Session_impl::close()`` is a ``mysql_close()`` with no thread-affine state
    of its own. The invariant that does matter is one thread at a time per
    session, which is what the connection's lock is for. See
    ``tests/unit/test_db_threading.py``, which pins this against a real server.

    Returns:
        None
    """
    # Waiting on the stop signal rather than sleeping, so that stopping the
    # reaper does not have to wait out an interval it is in the middle of.
    while not _reaper_stop.wait(_REAP_INTERVAL):
        try:
            _drop_expired_connections()
            _close_idle_sessions()
        except Exception as error:  # noqa: BLE001 - reaper must never die
            # Swallowed, so one bad pass cannot end the thread and leave the
            # two limits quietly not being applied any more - but reported, for
            # the same reason.
            general.log_event(
                "db: a connection reaper pass failed, the reaper keeps "
                f"running: {type(error).__name__}: {error}"
            )


def start_connection_reaper() -> None:
    """Starts the connection reaper.

    Called by :func:`mcp_plugin.lib.server.start` when it is about to serve over
    HTTP, which is the only place that knows a server is beginning. It used to be
    started by the first ``db.connect`` instead, which made a server-lifetime
    thread the side effect of a tool call: nothing ever stopped it, a second
    server in the same process silently kept the first one's thread, and the
    thread was spawned while the connection cache's own lock was held - the lock
    the new thread then wants.

    Over stdio it is not started at all. The one client owns the server process,
    so a connection it abandons outlives it by nothing, and the maximum lifetime
    does not depend on the reaper anyway: it is applied whenever a connection is
    used, in every transport (see :func:`_get_connection`). An embedder serving
    the tools itself without going through ``server.start`` gets no reaper, which
    is what it got before this too.

    Returns:
        None
    """
    global _reaper

    with _reaper_lock:
        if _reaper is not None and _reaper.is_alive():
            return

        # Cleared before starting as well as after stopping, so a reaper that
        # once outlived its join cannot leave the next one told to stop.
        _reaper_stop.clear()

        # A daemon thread: it only ever closes sessions and drops connection
        # records, so it must not keep the process alive when the server stops.
        # Spawned under a lock of its own, not the cache lock - the thread wants
        # the cache lock, and starting it while holding that was an oddity worth
        # not having.
        _reaper = threading.Thread(
            target=_reap_connections,
            name="mcp-db-connection-reaper",
            daemon=True,
        )
        _reaper.start()


def stop_connection_reaper() -> None:
    """Stops the connection reaper, if one is running.

    Called when serving ends, so the thread belongs to the server that started
    it rather than to the process. Without this, a shell that serves twice keeps
    the first server's thread for the life of the shell, and a stopped server
    leaves something waking every interval for nothing.

    Returns:
        None
    """
    global _reaper

    with _reaper_lock:
        reaper, _reaper = _reaper, None

    if reaper is None:
        return

    _reaper_stop.set()
    # It waits on the signal, so this returns as soon as it notices, rather
    # than at the end of the interval it happened to be in.
    reaper.join(timeout=_REAP_INTERVAL)
    _reaper_stop.clear()


def _session_was_restarted(connection_id: str) -> bool:
    """Returns whether the tool call in progress had to open a new session.

    Only meaningful inside a :func:`use_session` block, which is where it is
    read: outside one it is False, so a caller that asks at the wrong moment is
    told nothing rather than something untrue.

    Args:
        connection_id (str): The UUID of the connection being used.

    Returns:
        True if this call opened a new session for the connection.
    """
    with _sessions_lock:
        connection = _sessions.get(connection_id)

    return connection is not None and connection.session_restarted


def _unique_column_labels(columns) -> list:
    """Returns the labels of a result's columns, made unique.

    Two columns can perfectly well carry the same label - ``SELECT a.id, b.id
    FROM a JOIN b``, or any query that aliases two expressions the same way - and
    a row is handed to the client as a dict. Without this the second column would
    land on the first one's key: its value dropped, and the ``columns`` list
    still naming both, so the loss looks like something the client did.

    The first column with a label keeps it; a later one gets ``_2``, ``_3`` and so
    on, checked against every label already emitted, so the suffix cannot land on
    a column that really is called ``id_2``. Where a duplicate label comes before
    a column genuinely named that way round, the genuine one is the one that
    moves - first come, first served, and deterministic either way.

    A qualified name would read better than a suffix, but there is nothing to
    build one from: ``get_table_name()``, ``get_table_label()`` and
    ``get_column_name()`` all come back empty for an aliased or computed column
    (measured on this build), so the rule would hold for some queries and not
    others.

    Args:
        columns: The column metadata objects from ``result.get_columns()``.

    Returns:
        One label per column, in order, no two the same.
    """
    labels = []
    taken = set()

    for column in columns:
        label = column.get_column_label()

        if label in taken:
            suffix = 2
            while f"{label}_{suffix}" in taken:
                suffix += 1
            label = f"{label}_{suffix}"

        taken.add(label)
        labels.append(label)

    return labels


def _read_result_set(result) -> dict:
    """Reads the result set a result is on now: its columns and every row.

    Args:
        result: The result object returned by ``session.run_sql``, on a
            result set that has data.

    Returns:
        A dict with the column labels (columns) and the rows (rows), each row
        a dict keyed by those labels.
    """
    columns = _unique_column_labels(result.get_columns())
    rows = []
    for row in result.fetch_all():
        item = {}
        # Read by POSITION, not by label. Asking for a field by name cannot
        # reach the second of two columns that share one - it answers with
        # the first every time - so a value would be lost before there was
        # anything to key it on.
        for index, column in enumerate(columns):
            value = row[index]
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

    return {"columns": columns, "rows": rows}


def _serialize_result(
    result,
    session_restarted: bool = False,
    statement_index: Optional[int] = None,
    execution_time: Optional[float] = None,
) -> dict:
    """Serializes a shell SQL result into a JSON-friendly dict.

    Args:
        result: The result object returned by ``session.run_sql``.
        session_restarted (bool): Whether this statement ran on a session that
            had to be opened for it, which the caller is told about because
            nothing that only lived in the previous session survived.
        statement_index (int): Which statement of a script this is, counting
            only the non-empty ones from 0. Left out for a single statement,
            where there is nothing to count.
        execution_time (float): How long this statement took, in seconds.

    Returns:
        A dict with the result set (columns and rows) and execution metadata.
        A statement that returned more than one result set - a CALL of a
        procedure that runs several SELECTs - has its first as columns and
        rows and the others, in order, as additional_result_sets.
    """
    output = {
        "affected_items_count": result.affected_items_count,
    }

    if statement_index is not None:
        output["statement_index"] = statement_index

    if execution_time is not None:
        # Rounded to microseconds: the extra digits a float carries are
        # noise from the clock, not measurement.
        output["execution_time"] = round(execution_time, 6)

    # Present only when it happened. A field that is almost always false is a
    # field a client learns to skip.
    if session_restarted:
        output["session_restarted"] = True

    # A statement can return several result sets - a CALL returns one per
    # SELECT the procedure runs, then a status that carries none - and every
    # one of them is read. The first stays where a single statement's has
    # always been, so a client that knows nothing of the rest still reads it;
    # the others follow in order. Reading only the first would drop the rest
    # without a word.
    result_sets = []
    while True:
        if result.has_data():
            result_sets.append(_read_result_set(result))
        next_result = getattr(result, "next_result", None)
        if next_result is None or not next_result():
            break

    if result_sets:
        output["columns"] = result_sets[0]["columns"]
        output["rows"] = result_sets[0]["rows"]
    if len(result_sets) > 1:
        output["additional_result_sets"] = result_sets[1:]

    # Counted and read only NOW, after the rows above have been fetched. This
    # is mysql_warning_count, which for a statement that returns a result set
    # is not final until that set has been consumed - asking before the fetch
    # answered 0 for every SELECT, however many warnings it went on to report.
    output["warnings_count"] = result.warnings_count
    warnings = _serialize_warnings(result)
    if warnings:
        output["warnings"] = warnings

    return output


def _serialize_warnings(result) -> list:
    """Reads the warnings a statement produced off its result.

    Only called once the result set has been consumed, since the warnings are
    not all there before it has been.

    Args:
        result: The result object returned by ``session.run_sql``.

    Returns:
        One dict per warning, each with level, code and message. Empty where
        the statement produced none, or where the shell serving this session
        is too old to report them.
    """
    # A shell that does not have get_warnings still has warnings_count, so the
    # count is reported either way and only the texts go missing.
    reader = getattr(result, "get_warnings", None)
    if reader is None:
        return []

    # Read by ATTRIBUTE. A warning comes back as a shell Row, the same type a
    # query result row is, and subscripting one takes the sequence path - so
    # warning["level"] is an index, and raises rather than naming the field.
    warnings = []
    for warning in reader() or []:
        warnings.append({
            "level": warning.level,
            "code": warning.code,
            "message": warning.message,
        })

    return warnings


def _abbreviate(text: str, limit: int = 2000) -> str:
    """Shortens a statement for reporting it back.

    A failing statement is echoed so the caller can see what broke, but a
    generated script can hold a single statement of any size, and the whole
    of one would swamp the result.

    Args:
        text (str): The statement text.
        limit (int): The longest text to return, before the marker.

    Returns:
        The text, truncated with a marker if it was too long.
    """
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped

    return stripped[:limit] + "... (truncated)"


def _is_comment_only(statement: str) -> bool:
    """Whether a split-out statement holds nothing but a line comment.

    The shell's splitter returns a whole-line ``--`` or ``#`` comment as a
    statement of its own, so ``-- a note`` before a query is two statements
    there, not one. That is deliberate on its side - the interactive shell
    needs comment lines surfaced as units to echo them - but the comment
    carries no SQL, and the server accepts one as a query rather than
    rejecting it, so running it produces a result for a statement nobody
    wrote.

    Only a comment that OPENS a statement is split out this way, and the
    range the splitter returns for one ends at that line, so a statement
    beginning with a line comment is a statement that is nothing else. A
    comment inside a statement (``SELECT 1 -- note`` and the line after it)
    stays with it, and ``/* ... */`` in any of its forms - including the
    ``/*!...*/`` and ``/*+...*/`` ones, which DO carry SQL - is never split
    out at all.

    Args:
        statement (str): One statement as the splitter returned it.

    Returns:
        True if it is only a line comment, and so nothing to run.
    """
    stripped = statement.lstrip()
    if stripped.startswith("#"):
        return True

    # `--` opens a comment only when whitespace or the end of the text
    # follows it; `1--2` is a subtraction of a negative.
    return stripped.startswith("--") and (
        len(stripped) == 2 or stripped[2].isspace()
    )


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


def _drop_connections_on(uri: str, kind: str) -> int:
    """Ends every open connection that was opened on one configured connection.

    Called when that connection is deleted, so that deleting it takes effect at
    once rather than at the next time a session happens to be reopened. Without
    this a UUID handed out before the deletion goes on working for as long as
    its session lives - which for a connection in steady use is until it reaches
    CONNECTION_MAX_LIFETIME.

    Args:
        uri (str): The configured connection URI, as it is stored.
        kind (str): The connection list it was stored in.

    Returns:
        How many open connections were dropped.
    """
    with _sessions_lock:
        connection_ids = [
            connection_id
            for connection_id, connection in _sessions.items()
            if connection.uri == uri and connection.kind == kind
        ]

    for connection_id in connection_ids:
        _drop_connection(
            connection_id, f"its connection '{uri}' ({kind}) was deleted"
        )

    return len(connection_ids)


def _register_connection_management_tools(tool) -> None:
    """Registers the connection tools a server started with ``--gui`` serves.

    Only in GUI mode, which is what makes the connection list writable at all:
    everywhere else it is curated with ``mcp.setup``, deliberately out of reach
    of the clients that use it, so that a client cannot give itself credentials
    for a server nobody configured. The extension is not such a client - it is
    the user interface the user is entering those credentials into (see
    :func:`mcp_plugin.lib.general.set_gui_mode`).

    ``db.test_connection`` is here for the same reason and not only for
    symmetry: it opens a session to whatever host and credentials it is handed,
    which is an outbound connection primitive that has nothing to do with the
    configured connections. Given to an autonomous client it would be a way to
    try passwords against any reachable server, so it exists only where the
    client is the user's own editor.

    These tools take the connection list to work on as a ``kind``, because the
    extension works with both: the shared ``mcp`` list, which every MCP client
    sees and ``mcp.setup`` curates, and the ``gui`` list, which is the
    extension's own. It defaults to ``mcp``, so a call that does not say means
    the same list every other caller means.

    Args:
        tool: The registrar decorator factory to register with.

    Returns:
        None
    """

    @tool(name="db.list_connections")
    def list_connections(kind: str = config.DEFAULT_CONNECTION_KIND) -> list:
        """Lists the configured database connections of one kind.

        Args:
            kind: Which list of connections to return. "mcp" (the default) is
                the shared list that mcp.setup curates and every MCP client can
                open; "gui" is the list the MariaDB VS Code extension manages
                for itself with db.add_connection and db.delete_connection;
                "all" returns both in one call, the "mcp" list first.

        Returns:
            One entry per connection, each an object with the connection
            "uri" - which can be passed to db.connect - the "path" of the
            folder it is filed in ("/" for the top level, for example
            "/Sandboxes/note_app" for a nested folder), and the "kind" of the
            list it is in, "mcp" or "gui".
        """
        return config.list_connections_with_paths(kind)

    @tool(name="db.add_connection")
    def add_connection(
        uri: str,
        password: str = "",
        kind: str = config.DEFAULT_CONNECTION_KIND,
        verify: bool = True,
        path: str = None,
    ) -> str:
        """Stores a database connection and its password.

        The password goes into the operating system's secret store rather than
        into any file, and the URI is stored normalized, which is the spelling
        db.list_connections then reports.

        Storing a connection that is already in that list updates its password
        rather than failing, so this is safe to call again.

        Args:
            uri: The connection URI to store, for example
                mariadb://user@host:3306. A URI with no scheme is stored as
                mariadb://, the MariaDB client-server protocol; give
                mariadb+ssh:// to reach the server through an SSH tunnel. It
                must NOT carry a password - normalization strips one, so it
                would be stored without a password at all; pass it as the
                password argument instead.
            password: The password to store. An empty string is stored as an
                empty password, which is what a server with no password for
                that account wants.
            kind: Which list to store it in. "mcp" (the default) is the shared
                list that every MCP client can open; "gui" is the list the
                MariaDB VS Code extension manages for itself.
            verify: Whether to open a session with the credentials first and
                store them only if that works, which is the default. Pass false
                to store a connection to a server that is not up yet.
            path: The folder to file the connection in, such as "/Sandboxes"
                or "/Sandboxes/note_app" for a folder inside another. "/" or
                leaving it out means the top level, except that a connection
                already stored keeps its folder when this is left out. A '/'
                separates folders, so a folder name cannot contain one, and it
                cannot contain a ':' either.

        Returns:
            The normalized connection URI the connection was stored under.
        """
        kind = config.normalize_connection_kind(kind)
        # Checked before anything is verified or written, so a bad folder
        # name costs nothing.
        folder = None if path is None else config.normalize_connection_path(path)

        # Refused rather than used or quietly dropped, as mcp.setup refuses it:
        # normalization strips a password out of the URI, so using it would
        # mean storing a connection with no password at all.
        parsed = config.parse_connection_uri(uri)
        if parsed is not None and parsed.get("password"):
            raise mysqlsh.Error(
                "The connection URI carries a password. Give the URI without "
                "it and pass the password as the 'password' argument."
            )

        normalized = config.normalize_connection_uri(uri)
        if normalized is None:
            raise mysqlsh.Error(f"'{uri}' is not a valid connection URI.")

        if verify:
            # The same check mcp.setup makes, through the same function: a
            # connection has to be accepted on identical terms however it was
            # added. Imported here rather than at module scope to keep the
            # setup modules out of the import graph of the tools.
            from mcp_plugin.lib import setup_cli

            try:
                setup_cli.verify_connection(normalized, password)
            except Exception as error:  # noqa: BLE001 - surface the shell's text
                raise mysqlsh.Error(
                    f"Could not connect to '{normalized}': {error}. The "
                    "connection was not stored. Pass verify false to store it "
                    "anyway."
                ) from error

        # Asked before storing, since storing is what makes it true.
        replaced = normalized in config.list_stored_connection_uris(kind)

        config.store_connection(normalized, password, kind, folder)

        # Any other spelling of the same connection goes, which is how a
        # connection configured before the scheme was kept moves onto its new
        # key instead of ending up configured twice over. What was open on the
        # old key is closed, as it is for a deletion: that key is gone.
        superseded = config.drop_superseded_spellings(normalized, kind)
        dropped = sum(
            _drop_connections_on(old_uri, kind) for old_uri in superseded
        )
        replaced = replaced or bool(superseded)

        general.log_event(
            f"db.add_connection: {'updated' if replaced else 'stored'} "
            f"'{normalized}' ({kind})"
            + (
                f", superseding {', '.join(superseded)} and dropping "
                f"{dropped} open connection(s)"
                if superseded
                else ""
            )
        )

        return normalized

    @tool(name="db.update_connection")
    def update_connection(
        uri: str,
        new_uri: str = None,
        kind: str = config.DEFAULT_CONNECTION_KIND,
        new_kind: str = None,
        password: str = None,
        new_path: str = None,
    ) -> str:
        """Re-keys a configured connection, keeping its password.

        An editor needs this because a connection is keyed by its URI and by
        which list it is in, so changing the host, the port, the user or the
        MCP checkbox means storing it under a new key. Doing that with
        db.add_connection would mean knowing the password, and nothing can read
        one back - deliberately, since a tool that returned a stored password
        would hand every secret to any client that can call it. This moves the
        secret without anybody seeing it.

        Every connection open on the old key is closed, as it is by
        db.delete_connection: the connection it was opened on no longer exists
        under that name.

        Args:
            uri: The connection to change, as it is configured now.
            new_uri: The URI to move it to. Left out, the URI does not change.
                It must not carry a password.
            kind: Which list it is in now. "mcp" (the default) or "gui".
            new_kind: The list to move it to, which is how the MCP access
                checkbox is applied to a connection that already exists. Left
                out, the list does not change.
            password: A new password. Left out, the stored one is kept - which
                is the point of this tool.
            new_path: The folder to move it to, such as "/Sandboxes"; "/" is
                the top level. Left out, it stays in the folder it is in,
                including when it moves to the other list.

        Returns:
            The URI the connection is now configured under.
        """
        kind = config.normalize_connection_kind(kind)
        target_kind = config.normalize_connection_kind(
            kind if new_kind is None else new_kind
        )
        target_path = (
            None if new_path is None else config.normalize_connection_path(new_path)
        )

        configured_uri = config.resolve_connection_uri(uri, kind)
        if configured_uri is None:
            configured = config.list_connection_uris(kind)
            raise mysqlsh.Error(
                f"'{uri}' is not a configured '{kind}' connection. Configured "
                f"connections: {', '.join(configured) or 'none'}."
            )

        if new_uri is None:
            target_uri = configured_uri
        else:
            parsed = config.parse_connection_uri(new_uri)
            if parsed is not None and parsed.get("password"):
                raise mysqlsh.Error(
                    "The connection URI carries a password. Give the URI "
                    "without it and pass the password as the 'password' "
                    "argument."
                )

            target_uri = config.normalize_connection_uri(new_uri)
            if target_uri is None:
                raise mysqlsh.Error(
                    f"'{new_uri}' is not a valid connection URI."
                )

        current_path = config.get_connection_path(configured_uri, kind)
        # The folder it keeps, when none was asked for - including across a
        # move to the other list, which is a checkbox and not a re-filing.
        if target_path is None:
            target_path = current_path

        if target_uri == configured_uri and target_kind == kind:
            if password is None and target_path == current_path:
                # Nothing to do, and saying so beats a delete-then-store that
                # briefly leaves the connection not configured at all.
                return configured_uri

            # The same key, or the same URI in another folder, which
            # store_connection moves - the secret goes along either way,
            # read here and never returned.
            config.store_connection(
                configured_uri,
                (
                    config.get_connection_password(configured_uri, kind)
                    if password is None
                    else password
                ),
                kind,
                target_path,
            )
            general.log_event(
                "db.update_connection: "
                + (
                    "replaced the password of "
                    if password is not None
                    else "re-filed "
                )
                + f"'{configured_uri}' ({kind})"
                + (
                    f" in '{target_path or config.ROOT_CONNECTION_PATH}'"
                    if target_path != current_path
                    else ""
                )
            )

            return configured_uri

        # Read here and never returned: this is the one place the stored
        # password is touched, and it goes straight back into the store.
        moved_password = (
            config.get_connection_password(configured_uri, kind)
            if password is None
            else password
        )

        # Written before the old one is removed, so a failure leaves the
        # connection configured somewhere rather than nowhere. Storing may have
        # taken the old key with it, where the new URI is another spelling of
        # the same connection - a connection configured before the scheme was
        # kept and saved again under it is exactly that - and deleting a secret
        # that is not there raises, so it only goes if it survived.
        config.store_connection(
            target_uri, moved_password, target_kind, target_path
        )
        superseded = config.drop_superseded_spellings(target_uri, target_kind)
        if target_kind != kind or configured_uri not in superseded:
            config.delete_connection(configured_uri, kind)

        dropped = _drop_connections_on(configured_uri, kind)
        dropped += sum(
            _drop_connections_on(old_uri, target_kind)
            for old_uri in superseded
            if old_uri != configured_uri
        )

        general.log_event(
            f"db.update_connection: moved '{configured_uri}' ({kind}) to "
            f"'{target_uri}' ({target_kind}), dropping {dropped} open "
            "connection(s)"
        )

        return target_uri

    @tool(name="db.test_connection")
    def test_connection(uri: str, password: str = None) -> str:
        """Checks that a URI and password open a session, storing nothing.

        This is what an editor's "Test Connection" button needs: whether the
        credentials work has to be answerable BEFORE the connection is stored,
        and db.connect cannot answer it because it only opens connections that
        are already configured.

        The session is closed again immediately and nothing is written, so a
        failed test leaves no trace and a successful one still requires
        db.add_connection.

        Args:
            uri: The connection URI to try, for example
                mariadb://user@host:3306. A URI with no scheme is read as
                mariadb://. As with db.add_connection it must not carry a
                password.
            password: The password to try. Left out, the password already
                stored for this connection is used - which is what lets an
                editor test a connection the user has not retyped the password
                for. The URI must then name a configured connection.

        Returns:
            A message saying the connection was opened.
        """
        parsed = config.parse_connection_uri(uri)
        if parsed is not None and parsed.get("password"):
            raise mysqlsh.Error(
                "The connection URI carries a password. Give the URI without "
                "it and pass the password as the 'password' argument."
            )

        normalized = config.normalize_connection_uri(uri)
        if normalized is None:
            raise mysqlsh.Error(f"'{uri}' is not a valid connection URI.")

        if password is None:
            # Read here and used here; like db.update_connection this is a
            # place the stored secret is touched without ever being returned.
            found = config.find_connection(normalized)
            if found is None:
                raise mysqlsh.Error(
                    f"'{uri}' is not a configured connection, so there is no "
                    "stored password to test it with. Pass the password to "
                    "test a connection that does not exist yet."
                )

            configured_uri, kind = found
            password = config.get_connection_password(configured_uri, kind)

        # The same check db.add_connection and mcp.setup make, so a test that
        # passes cannot be followed by a store that fails for a reason the
        # test would have caught.
        from mcp_plugin.lib import setup_cli

        try:
            setup_cli.verify_connection(normalized, password)
        except Exception as error:  # noqa: BLE001 - surface the shell's text
            raise mysqlsh.Error(
                f"Could not connect to '{normalized}': {error}"
            ) from error

        general.log_event(f"db.test_connection: opened '{normalized}' and closed it")

        return f"Connected to '{normalized}' successfully."

    @tool(name="db.delete_connection")
    def delete_connection(
        uri: str, kind: str = config.DEFAULT_CONNECTION_KIND
    ) -> str:
        """Deletes a stored database connection and its password.

        Every connection open on it is closed as well, so the deletion takes
        effect at once and the UUIDs db.connect handed out for it stop working.

        Args:
            uri: The connection to delete. It does not have to be spelled
                exactly as db.list_connections reports it, only name the same
                connection.
            kind: Which list to delete it from. "mcp" (the default) is the
                shared list that every MCP client can open; "gui" is the list
                the MariaDB VS Code extension manages for itself. A connection
                is only deleted from the list named here, even where the other
                list holds the same URI.

        Returns:
            The connection URI that was deleted, as it was stored.
        """
        kind = config.normalize_connection_kind(kind)

        configured_uri = config.resolve_connection_uri(uri, kind)
        if configured_uri is None:
            configured = config.list_connection_uris(kind)
            raise mysqlsh.Error(
                f"'{uri}' is not a configured '{kind}' connection. Configured "
                f"connections: {', '.join(configured) or 'none'}."
            )

        config.delete_connection(configured_uri, kind)
        dropped = _drop_connections_on(configured_uri, kind)

        general.log_event(
            f"db.delete_connection: deleted '{configured_uri}' ({kind}), "
            f"dropping {dropped} open connection(s)"
        )

        return configured_uri


def register_db_tools(server, function_groups=()) -> None:
    """Registers the database connection tools on the given server.

    Which tools those are depends on GUI mode: a server started with ``--gui``
    also serves ``db.add_connection`` and ``db.delete_connection``, and its
    ``db.list_connections`` takes the connection list to report (see
    :func:`_register_connection_management_tools`). GUI mode is read once, here,
    because it decides what the server ADVERTISES - so it has to be settled
    before a client asks.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served. Unused here,
            as none of the db tools depend on another group.

    Returns:
        None
    """
    from mcp.server.mcpserver import Context

    tool = tool_registrar(server)

    if general.is_gui_mode():
        _register_connection_management_tools(tool)
    else:
        @tool(name="db.list_connections")
        def list_connections() -> list:
            """Lists the configured database connection URIs.

            Returns:
                The list of connection URIs configured via mcp.setup. Any of
                these can be passed to db.connect.
            """
            return config.list_connection_uris()

    @tool(name="db.connect")
    def connect(ctx: Context, uri: str) -> str:
        """Opens a configured database connection and caches it.

        The connection is usable until it is closed with db.close, or until it
        expires - a few hours after it was opened, whichever comes first. Once
        it has expired the UUID no longer works and this tool has to be called
        again for a new one; the URI is checked against the configured
        connections each time, so a connection that has been removed in the
        meantime is not opened again.

        Its database session is also closed whenever it has been unused for a
        while, but that leaves the connection itself usable: the next call opens
        a new session automatically, so no state that only lived in the previous
        session - temporary tables, session variables, the current schema or an
        open transaction - survives an idle period.

        When the server is served over HTTP the connection can only be used by
        the client that opened it.

        Only a limited number of connections can be open at a time, so close the
        ones that are no longer needed with db.close rather than opening another
        for every task.

        Args:
            uri: A connection URI, as returned by db.list_connections. It does
                not have to be spelled exactly as it is listed: a left-out
                scheme (which means mariadb://) and a left-out default port name
                the same connection. Anything the configured connection does not
                name - a default schema, a connection option, another scheme -
                does not, as it would not be applied.

        Returns:
            The UUID identifying the open connection. Pass it to
            db.execute_sql and db.close.
        """
        # Resolved to the spelling the connection is configured under, which is
        # the key the password is read under and the URI everything from here on
        # works with: the session is opened on it, it is re-validated against
        # the configuration on every open, and it is what the log says. The list
        # it was found in comes back with it and is kept for the same reason -
        # in GUI mode there are two, and the URI alone does not say which.
        found = config.find_connection(uri)
        if found is None:
            raise mysqlsh.Error(
                f"'{uri}' is not a configured connection. Use db.list_connections "
                "to list the available connections, or configure it with mcp.setup."
            )

        configured_uri, kind = found

        # The connection belongs to the client that opens it. Without both the
        # peer address and the MCP session id to bind it to there is no way to
        # keep another client from using it, so serving over HTTP requires both.
        client = general.get_client_identity(ctx)
        if general.is_http_transport() and (
            client.address is None or client.session_id is None
        ):
            general.log_event(
                "db.connect: REFUSED to open a connection for a request that "
                f"could not be fully identified ({general.describe_client(client)})"
            )

            raise mysqlsh.Error(
                "The client could not be identified, so the connection cannot "
                "be bound to it. Over HTTP a connection can only be opened on "
                "an established MCP session, by a client whose address the "
                "server can determine."
            )

        connection = _Connection(configured_uri, client, kind)
        connection_id = str(uuid.uuid4())

        # Room is claimed before the session is opened, so a call that is going
        # to be refused for being over the limit does not cost the database a
        # connection on the way to being told so.
        _claim_connection_slot(connection_id, connection)

        try:
            # Opened right away, so a bad password or an unreachable server is
            # reported by db.connect rather than by the first tool using it.
            connection.open_session()
        except Exception:
            # The slot goes back: nothing was handed out, so nothing may be left
            # holding a place in the cache.
            with _sessions_lock:
                _sessions.pop(connection_id, None)

            raise

        # The line every later one about this connection refers back to: which
        # client it was bound to, and which stored credentials it was opened on.
        general.log_event(
            f"db.connect: opened connection {general.log_id_prefix(connection_id)} "
            f"on '{configured_uri}' ({kind}) for "
            f"{general.describe_client(client)}"
        )

        return connection_id

    @tool(name="db.list_schemas")
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
        with use_session(connection_id, general.get_client_identity(ctx)) as session:
            return _query_rows(session, _LIST_SCHEMAS_SQL)

    @tool(name="db.list_objects")
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

        with use_session(connection_id, general.get_client_identity(ctx)) as session:
            return _query_rows(session, sql, [schema_name])

    @tool(name="db.get_object_details")
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

        with use_session(connection_id, general.get_client_identity(ctx)) as session:
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

    @tool(name="db.execute_sql")
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
            metadata. Each row is keyed by the column labels listed in columns.
            Where a query selects two columns with the same label - joining two
            tables that both have an id, say - the later one is listed and keyed
            as label_2 (then _3, and so on), so that no column is lost.
            A CALL of a procedure that returns several result sets has its
            first as columns and rows and the others, in order, as
            additional_result_sets - a list of dicts with columns and rows.

            warnings_count is how many warnings the statement produced, and
            warnings - present only when there were any - lists them, each
            with its level, code and message, as SHOW WARNINGS would report
            them.

            If it contains session_restarted: true, this statement ran on a
            newly opened database session, because the previous one had been
            closed for being idle or lost. Nothing that only lived in that
            session is still there: no temporary tables, no session variables,
            no current schema - and no open transaction. A COMMIT or ROLLBACK
            reported as successful alongside this flag committed or rolled back
            nothing, and any statements of that transaction are gone. Start the
            work again on this connection.
        """
        with use_session(connection_id, general.get_client_identity(ctx)) as session:
            result = session.run_sql(sql, params if params is not None else [])

            return _serialize_result(
                result, _session_was_restarted(connection_id)
            )

    @tool(name="db.execute_sql_script")
    def execute_sql_script(
        ctx: Context,
        connection_id: str,
        sql_script: Optional[str] = None,
        file_path: Optional[str] = None,
        stop_on_error: bool = True,
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
            stop_on_error: Whether a failing statement ends the script.
                True (the default) runs no further statements once one
                fails, which is what a script whose later statements build
                on its earlier ones needs. False runs every statement and
                reports each failure, which suits a script of independent
                statements - a batch of inserts, or a set of DROPs - where
                one failure should not hold up the rest.

        Returns:
            A list with one entry per statement that ran, each a dict with the
            result set (columns and rows) and execution metadata: its position
            in the script (statement_index, counting the non-empty statements
            from 0) and how long it took (execution_time, in seconds). Two
            columns sharing one label are keyed apart as label and label_2, as
            for db.execute_sql. Each entry also carries warnings_count, and
            warnings - the level, code and message of each - where the
            statement produced any. A statement that returned several result
            sets - a CALL of a procedure that runs more than one SELECT - has
            the first as columns and rows and the others, in order, as
            additional_result_sets (a list of dicts with columns and rows).

            A statement that is nothing but a -- or # line comment is NOT run
            and has no entry, since it carries no SQL. It still takes up a
            statement_index, so the indexes of the statements around it are
            what they would be if it had run - a caller that split the script
            for itself can go on pairing results with statements by this
            number.

            IMPORTANT - a failing statement does NOT raise. Its entry carries
            error (the message) and statement (the text that failed) INSTEAD
            of a result set, so check every entry for an error key before
            treating the script as done. Nothing is rolled back either way: a
            script is not a transaction, and the statements that already ran
            have taken effect.

            With stop_on_error true (the default) the script ends at the first
            failure, and the statements after it have no entries because they
            never ran - so fewer entries than statements means it stopped
            early, or that the script held a comment. With stop_on_error false
            every statement is attempted and there is one entry per statement
            that ran, error or not.

            If the FIRST entry contains session_restarted: true, the script ran
            on a newly opened database session, because the previous one had
            been closed for being idle or lost. Nothing that only lived in that
            session is still there - temporary tables, session variables, the
            current schema, and any open transaction are all gone, so a COMMIT
            in this script committed nothing that came before it. The flag is on
            the first entry only, as the session is opened once, before the
            script starts.
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

        with use_session(connection_id, general.get_client_identity(ctx)) as session:
            restarted = _session_was_restarted(connection_id)
            statements = [
                statement
                for statement in mysqlsh.mysql.split_script(sql_script)
                if statement.strip() != ""
            ]
            results = []
            for index, statement in enumerate(statements):
                # A whole-line comment is one of these, and running it would
                # report a result for a statement nobody wrote. It keeps its
                # index all the same: a caller that split the script for
                # itself pairs results with statements by this number, and
                # renumbering would move every result after the comment onto
                # the wrong statement.
                if _is_comment_only(statement):
                    continue

                started = time.perf_counter()
                try:
                    result = session.run_sql(statement, [])
                except Exception as error:  # noqa: BLE001
                    # Reported rather than raised, because what already ran
                    # matters: a script is not a transaction, and a caller
                    # that only sees "it failed" cannot tell how far it got
                    # or which statement to fix.
                    general.log_event(
                        "db.execute_sql_script: statement "
                        f"{index} failed on connection "
                        f"{general.log_id_prefix(connection_id)}: {error}"
                    )
                    results.append(
                        {
                            "statement_index": index,
                            "execution_time": round(
                                time.perf_counter() - started, 6
                            ),
                            "statement": _abbreviate(statement),
                            "error": str(error),
                        }
                    )

                    if stop_on_error:
                        break

                    continue

                results.append(
                    _serialize_result(
                        result,
                        # On the first result only: one session was opened, once,
                        # before any of these statements ran.
                        session_restarted=restarted and not results,
                        statement_index=index,
                        execution_time=time.perf_counter() - started,
                    )
                )

            return results

    @tool(name="db.close")
    def close(ctx: Context, connection_id: str) -> None:
        """Closes an open database connection.

        Args:
            connection_id: The UUID returned by db.connect.

        Returns:
            None
        """
        connection = _get_connection(
            connection_id, general.get_client_identity(ctx)
        )

        with _sessions_lock:
            _sessions.pop(connection_id, None)

        # Ends the connection, closing its session if one is open - a session
        # already closed for being idle is not opened again just to close it.
        # This also settles what happens to a tool call that resolved this
        # connection a moment ago and is about to lock it: it is refused, rather
        # than opening a new session on a connection that is no longer in the
        # cache and that nothing would ever close.
        connection.close()
