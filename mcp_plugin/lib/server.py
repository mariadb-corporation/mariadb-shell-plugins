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

"""MCP server lifecycle.

The MCP server is meant to be launched from the command line, e.g.

    mariadb-shell -- mcp start-server --port=8080

It builds an MCPServer, registers the requested function groups on it - the
database tools (see :mod:`mcp_plugin.lib.db_functions`) and/or the MariaDB
Schema Management tools (see :mod:`mcp_plugin.lib.msm_functions`), which can be
loaded independently - and serves it in the foreground using one of two
transports:

* ``streamable-http`` (default): served over HTTP on the configured host/port,
  on a uvicorn server configured here rather than by the SDK, so that the
  proxy-header handling that would let a client choose the peer address it is
  seen as stays off (see :func:`_serve_streamable_http`).
* ``stdio``: communicates over stdin/stdout; its lifetime is driven by the
  client. The real stdout is reserved for the JSON-RPC protocol and all other
  output is redirected to stderr (see :func:`_serve_stdio`).

The transport in use is recorded via
:func:`mcp_plugin.lib.general.set_active_transport` before serving starts. Over
HTTP the server is reachable by more than one client, so a database connection
is only opened for a client whose address can be determined (see
:mod:`mcp_plugin.lib.db_functions`); over stdio, where there is only ever the one
client that owns the server process, that does not apply. That an open connection
may only be used from the address it was opened from is not tied to the recorded
transport - it holds either way.

Serving over HTTP also owns the lifetime of the connection reaper, which closes
sessions that have fallen idle and drops connections that have reached their
maximum lifetime: :func:`start` starts it before serving and stops it when
serving ends, so the thread belongs to the server rather than to whichever tool
call happened to be the first to need it.

The shell's interactive mode is disabled before serving, so the wrapped ``msm``
plugin functions return their results instead of prompting for input.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver streamable fdopen dup2 uvicorn starlette

import os
import sys

import mysqlsh

from mcp_plugin.lib import (
    db_functions,
    general,
    migrator_functions,
    msm_functions,
    sandbox_functions,
)


# Maps a function group name to the callback that registers its tools.
_FUNCTION_GROUP_REGISTRARS = {
    general.FUNCTION_GROUP_DB: db_functions.register_db_tools,
    general.FUNCTION_GROUP_MSM: msm_functions.register_msm_tools,
    general.FUNCTION_GROUP_SANDBOX: sandbox_functions.register_sandbox_tools,
    general.FUNCTION_GROUP_MIGRATOR: migrator_functions.register_migrator_tools,
}


def build_mcp_server(function_groups):
    """Builds and configures the MariaDB MCP server.

    The host and port are not part of the server itself; they are transport
    options passed when the server is served (see :func:`start`).

    Args:
        function_groups (list): The function groups whose tools should be
            registered on the server.

    Returns:
        The configured MCPServer instance.
    """
    # Imported lazily so that the plugin can be loaded even when the optional
    # `mcp` dependency is not available.
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("MariaDB MCP Server")
    # The full list of enabled groups is handed to every registrar, so a group
    # can leave out the tools that depend on another group not being served.
    for group in function_groups:
        _FUNCTION_GROUP_REGISTRARS[group](server, function_groups)

    return server


def start(
    host: str, port: int, transport: str, function_groups, allowed_hosts=()
) -> None:
    """Builds and serves the MCP server using the given transport.

    Disables the shell's interactive mode and then serves the MCP server in the
    foreground, blocking for the lifetime of the server.

    Args:
        host (str): The host address to bind to (streamable-http only).
        port (int): The TCP port to listen on (streamable-http only).
        transport (str): The MCP transport to use, either "streamable-http" or
            "stdio".
        function_groups (list): The function groups whose tools should be
            exposed by the server.
        allowed_hosts: Additional Host header values to accept (streamable-http
            only), for a server reachable under a name that cannot be derived
            from the bind address (see :func:`_transport_security_settings`).

    Returns:
        None
    """
    if transport not in general.SUPPORTED_TRANSPORTS:
        raise mysqlsh.Error(
            f"Unsupported transport '{transport}'. Supported transports are: "
            f"{', '.join(general.SUPPORTED_TRANSPORTS)}."
        )

    if not function_groups:
        raise mysqlsh.Error(
            "At least one function group must be enabled. Supported function "
            f"groups are: {', '.join(general.SUPPORTED_FUNCTION_GROUPS)}."
        )

    unknown_groups = [
        group for group in function_groups if group not in _FUNCTION_GROUP_REGISTRARS
    ]
    if unknown_groups:
        raise mysqlsh.Error(
            f"Unknown function group(s): {', '.join(unknown_groups)}. Supported "
            f"function groups are: {', '.join(general.SUPPORTED_FUNCTION_GROUPS)}."
        )

    # Disable interactive mode so the wrapped msm functions return their
    # results instead of prompting for input.
    mysqlsh.globals.shell.options.useWizards = False

    # Recorded before anything is served: the transport decides whether the
    # database connections are bound to the client that opened them and closed
    # when they fall idle (see mcp_plugin.lib.db_functions).
    general.set_active_transport(transport)

    mcp_server = build_mcp_server(function_groups=function_groups)

    if transport == general.TRANSPORT_STDIO:
        _serve_stdio(mcp_server)
    else:
        _warn_if_reachable_from_the_network(host, port)

        # The reaper that closes idle sessions and drops expired connections
        # belongs to the server, and this is where a server begins and ends. It
        # used to be started by the first db.connect, which made a thread nobody
        # stopped the side effect of a tool call.
        db_functions.start_connection_reaper()
        try:
            _serve_streamable_http(mcp_server, host, port, allowed_hosts)
        finally:
            db_functions.stop_connection_reaper()


def _warn_if_reachable_from_the_network(host: str, port: int) -> None:
    """Warns when the server is about to be exposed beyond this machine.

    The MCP server has no authentication of its own: any client that can reach
    the port can list the configured connections and open one, using the
    credentials kept in the shell's secret store. Binding to loopback - the
    default - is what keeps that to clients on this machine. Binding anywhere
    else hands the same access to the network, which is worth saying out loud
    rather than leaving to be discovered.

    Written to stderr, which is where the shell's own diagnostics go and which
    keeps it clear of anything a client reads.

    Args:
        host (str): The host the server is about to bind to.
        port (int): The port the server is about to listen on.

    Returns:
        None
    """
    if general.is_loopback_host(host):
        return

    print(
        f"\nWARNING: the MariaDB MCP server is about to listen on {host}:{port}, "
        "which is reachable from other machines.\n"
        "         The server has NO AUTHENTICATION: anyone who can reach this "
        "port can list the\n"
        "         configured database connections and open them, using the "
        "stored credentials.\n"
        "         Bind to 127.0.0.1 (the default) and put a tunnel or an "
        "authenticating proxy in\n"
        "         front of it if it has to be reachable remotely.\n",
        file=sys.stderr,
        flush=True,
    )


def _serve_streamable_http(
    mcp_server, host: str, port: int, allowed_hosts=()
) -> None:
    """Serves the MCP server over streamable-http on our own uvicorn server.

    This is what ``MCPServer.run(transport="streamable-http")`` does, with two
    deliberate differences: uvicorn's proxy-header handling is turned OFF, and
    the transport's DNS-rebinding protection is configured explicitly rather
    than left to the SDK to guess at (see
    :func:`_transport_security_settings`).

    Uvicorn enables ``ProxyHeadersMiddleware`` by default, and that middleware
    overwrites the ASGI ``client`` entry - the peer address the connection
    binding in :mod:`mcp_plugin.lib.db_functions` relies on - with the value of
    the ``X-Forwarded-For`` header whenever the immediate peer is one of
    ``forwarded_allow_ips`` (``127.0.0.1`` by default). As this server binds to
    loopback by default, every request would qualify, and any client could pick
    the address it is seen as simply by sending the header. The SDK's own
    ``run()`` builds its ``uvicorn.Config`` internally and exposes no way to
    turn that off, so the app is served here instead.

    Do not swap this for the ``FORWARDED_ALLOW_IPS`` environment variable: what
    an empty trust list means is uvicorn-version-dependent, whereas
    ``proxy_headers=False`` keeps the middleware from being installed at all.

    Args:
        mcp_server: The MCPServer instance to serve.
        host (str): The host address to bind to.
        port (int): The TCP port to listen on.
        allowed_hosts: Additional Host header values to accept, for a server
            reachable under a name this cannot derive from the bind address.

    Returns:
        None
    """
    import anyio
    import uvicorn

    starlette_app = mcp_server.streamable_http_app(
        host=host,
        transport_security=_transport_security_settings(host, port, allowed_hosts),
    )

    config = uvicorn.Config(
        starlette_app,
        host=host,
        port=port,
        log_level=mcp_server.settings.log_level.lower(),
        proxy_headers=False,
    )

    anyio.run(uvicorn.Server(config).serve)


def _dialable_host_names(host: str) -> list:
    """Returns the names a client can reach a server bound to ``host`` by.

    These become the allowed values of the Host header. Which spelling a client
    dials is its own choice, so all of them have to be accepted: a loopback
    server answers to ``127.0.0.1``, ``localhost`` and ``[::1]`` alike.

    Args:
        host (str): The host the server binds to.

    Returns:
        The list of host names, without ports.
    """
    if general.is_wildcard_host(host):
        # Bound to every interface: reachable at loopback and under this
        # machine's own name and addresses. A name that merely resolves here -
        # a DNS alias, or the name of a proxy in front - cannot be derived and
        # has to be named with the allowed_hosts option.
        import socket

        names = list(general.LOOPBACK_HOST_NAMES)
        for name in (socket.gethostname(), socket.getfqdn()):
            if name and name not in names:
                names.append(name)

        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                address = info[4][0]
                # An IPv6 address is bracketed in a Host header.
                if ":" in address:
                    address = f"[{address}]"
                if address not in names:
                    names.append(address)
        except OSError:
            # Unresolvable hostname: the loopback names and the hostname
            # itself are all that can be offered.
            pass

        return names

    if general.is_loopback_host(host):
        # Every loopback spelling reaches the same server, whichever one it was
        # bound with. This also covers the addresses beyond 127.0.0.1 in
        # 127.0.0.0/8, which a client still reaches as one of these three.
        return list(general.LOOPBACK_HOST_NAMES)

    # A specific address or name: exactly that, bracketing a bare IPv6 address
    # as a Host header would carry it.
    host = host.strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    return [host]


def _transport_security_settings(host: str, port: int, allowed_hosts=()):
    """Builds the DNS-rebinding protection for the host being bound.

    Without this the protection is decided by the SDK, and the way it decides
    is worth not depending on: ``streamable_http_app`` turns it on only when no
    settings are passed AND the host is exactly one of ``"127.0.0.1"``,
    ``"localhost"`` or ``"::1"``. It is a case-sensitive comparison of literal
    strings, so ``LOCALHOST``, ``[::1]`` or any other address in 127.0.0.0/8
    silently serves with NO Host or Origin validation at all - and so does
    every non-loopback bind, which is where it would matter most. Passing
    settings explicitly takes that decision away from a string match.

    What it protects against: the server has no authentication (see
    :func:`_warn_if_reachable_from_the_network`), so a page in a browser that
    can reach it could otherwise drive the database tools. It cannot read the
    responses cross-origin, but a DNS-rebinding attack removes even that limit
    by resolving the attacker's own name to the address the server is on, which
    makes the page same-origin with it. Validating the Host header defeats
    that: the browser sends the name the page was loaded from, which is not one
    the server answers to.

    Requests without an Origin header stay allowed, as that is every non-browser
    client - a browser sets Origin on the POSTs the MCP transport makes.

    Args:
        host (str): The host the server binds to.
        port (int): The port the server listens on.
        allowed_hosts: Additional Host header values to accept, for a server
            reachable under a name that cannot be derived from the bind
            address - through a proxy, a port forward or a DNS alias.

    Returns:
        The TransportSecuritySettings to serve with.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    names = _dialable_host_names(host)

    hosts = []
    origins = []
    for name in names:
        # Both the bare name, as sent when the port is the scheme's default,
        # and the name at the port actually being served.
        for value in (name, f"{name}:{port}"):
            if value not in hosts:
                hosts.append(value)

        # Any port, since a browser page served from this same host is as
        # trusted as any other local client - and no more.
        for scheme in ("http", "https"):
            origins.extend((f"{scheme}://{name}", f"{scheme}://{name}:*"))

    for name in allowed_hosts or ():
        name = str(name).strip()
        if not name or name in hosts:
            continue
        hosts.append(name)
        # An entry may already carry a port; offer both forms as an origin.
        for scheme in ("http", "https"):
            origins.extend((f"{scheme}://{name}", f"{scheme}://{name}:*"))

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _serve_stdio(mcp_server) -> None:
    """Serves the MCP server over stdio, protecting the JSON-RPC stream.

    In stdio mode the JSON-RPC messages are exchanged over the process stdout.
    Any other output produced while a tool runs (shell progress messages,
    Python prints, or C-level writes to file descriptor 1) would corrupt that
    stream. To prevent this, the real stdout is duplicated and handed to the
    MCP transport, then file descriptor 1 and Python's ``sys.stdout`` are
    redirected to stderr for the lifetime of the server so stray output can
    never reach the client.

    Args:
        mcp_server: The MCPServer instance to serve.

    Returns:
        None
    """
    import io

    import anyio
    from mcp.server.stdio import stdio_server

    # Reserve the real stdout (fd 1) for the protocol.
    protocol_fd = os.dup(1)
    protocol_stream = io.TextIOWrapper(
        os.fdopen(protocol_fd, "wb"), encoding="utf-8"
    )

    # Redirect fd 1 (C-level writes) and Python's sys.stdout to stderr so that
    # any output produced while serving cannot corrupt the protocol stream.
    saved_sys_stdout = sys.stdout
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    async def _run():
        # stdio_server does not close the stream we pass in.
        async with stdio_server(stdout=anyio.wrap_file(protocol_stream)) as (
            read_stream,
            write_stream,
        ):
            await mcp_server._lowlevel_server.run(
                read_stream,
                write_stream,
                mcp_server._lowlevel_server.create_initialization_options(),
            )

    try:
        anyio.run(_run)
    finally:
        # Restore fd 1 from the reserved copy, then restore sys.stdout and
        # release the reserved stream (which closes protocol_fd).
        os.dup2(protocol_fd, 1)
        sys.stdout = saved_sys_stdout
        protocol_stream.close()
