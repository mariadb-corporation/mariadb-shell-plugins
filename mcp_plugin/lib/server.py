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

``--gui`` is recorded the same way, via
:func:`mcp_plugin.lib.general.set_gui_mode`, and before the tools are built
rather than merely before serving: it decides which tools the server has, so it
cannot be settled any later. It says the client is the MariaDB VS Code
extension, which is the one client that is a user interface rather than an
autonomous agent - see that function for what it turns on and why the answer is
different for it.

Multi-tenant mode is not an option of this function but part of the
configuration (``mcp setup --multiTenant=true``, see
:mod:`mcp_plugin.lib.tenants`), read here and recorded with
:func:`mcp_plugin.lib.general.set_multi_tenant` before the tools are built. A
multi-tenant server only serves over HTTP, only the ``db`` and ``msm`` groups,
never ``--gui``, and only to requests carrying a user's bearer token, which the
SDK checks before any tool runs (see :mod:`mcp_plugin.lib.auth`).
"""

# cSpell:ignore mysqlsh MariaDB mcpserver streamable fdopen dup2 uvicorn starlette

import os
import sys

import mysqlsh

# `db_functions` is imported here for the connection reaper that :func:`start`
# owns, not for the tool registration below - which resolves every group the
# same way, lazily. It costs nothing: `mcp_plugin.lib` imports it eagerly
# anyway, and unlike `migrator_functions` it pulls in no MCP SDK module at
# import time.
from mcp_plugin.lib import config, db_functions, general, tenants


# Maps a function group name to the (module, function) naming the callback that
# registers its tools. Resolved by :func:`_registrar` when a group is actually
# served, rather than imported here, because a tool module may import the MCP
# SDK at module scope: `migrator_functions` does, for ToolError. The shell
# imports this plugin eagerly, and pulling the SDK in that early binds
# `mcp.client.stdio.stdio_client`'s `errlog=sys.stderr` default to the shell's
# `mysqlsh.shell_stderr`, which has no usable `fileno()`. Measured: importing
# the plugin loads NO `mcp.*` module, and one `ToolError` import loads over a
# hundred of them, `mcp.client.stdio` included.
_FUNCTION_GROUP_REGISTRARS = {
    general.FUNCTION_GROUP_DB: ("db_functions", "register_db_tools"),
    general.FUNCTION_GROUP_MSM: ("msm_functions", "register_msm_tools"),
    general.FUNCTION_GROUP_SANDBOX: ("sandbox_functions", "register_sandbox_tools"),
    general.FUNCTION_GROUP_MIGRATOR: ("migrator_functions", "register_migrator_tools"),
}


def _registrar(group):
    """Returns the registrar callback for one function group.

    Args:
        group (str): The function group name.

    Returns:
        The ``register_*_tools`` callable for that group.
    """
    import importlib

    module_name, function_name = _FUNCTION_GROUP_REGISTRARS[group]
    module = importlib.import_module(f"mcp_plugin.lib.{module_name}")

    return getattr(module, function_name)


def build_mcp_server(function_groups, auth=None, tool_name_separator="."):
    """Builds and configures the MariaDB MCP server.

    The host and port are not part of the server itself; they are transport
    options passed when the server is served (see :func:`start`).

    Args:
        function_groups (list): The function groups whose tools should be
            registered on the server.
        auth: The :class:`mcp_plugin.lib.auth.AuthBundle` to authenticate every
            request with (see :func:`mcp_plugin.lib.auth.build_auth`), or None
            for a server that does not authenticate. An authenticating server
            also lists each caller only the tools their token grants.
        tool_name_separator (str): What separates a tool's group from its name
            in the names the tools are published under (see
            :func:`mcp_plugin.lib.config.get_tool_name_separator`).

    Returns:
        The configured MCPServer instance.
    """
    # Imported lazily so that the plugin can be loaded even when the optional
    # `mcp` dependency is not available.
    from mcp.server.mcpserver import MCPServer

    if auth is None:
        server = MCPServer("MariaDB MCP Server")
    else:
        from mcp_plugin.lib.auth import scoped_server_class

        server = scoped_server_class()("MariaDB MCP Server", **auth.server_kwargs())
    # The full list of enabled groups is handed to every registrar, so a group
    # can leave out the tools that depend on another group not being served.
    from mcp_plugin.lib import tool_registrar

    tool_registrar.use_tool_name_separator(server, tool_name_separator)
    for group in function_groups:
        _registrar(group)(server, function_groups)
    tool_registrar.finish_tool_names(server)

    return server


def start(
    host: str,
    port: int,
    transport: str,
    function_groups,
    allowed_hosts=(),
    gui: bool = False,
    ssl_certfile=None,
    ssl_keyfile=None,
    max_connections=None,
    public_url=None,
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
        gui (bool): Whether to serve for the MariaDB VS Code extension, which
            widens what a client may do (see
            :func:`mcp_plugin.lib.general.set_gui_mode`).
        ssl_certfile (str): A PEM certificate (chain) to serve HTTPS with,
            together with ssl_keyfile (streamable-http only).
        ssl_keyfile (str): The private key of ssl_certfile.
        max_connections (int): The most database connections the server holds
            open at once, for all clients together, or None for
            :data:`mcp_plugin.lib.general.MAX_CONNECTIONS_TOTAL`.
        public_url (str): The URL clients reach the MCP endpoint at, for OAuth
            (see :mod:`mcp_plugin.lib.oauth_config`), or None for the
            configured one.

    Returns:
        None
    """
    multi_tenant = tenants.is_multi_tenant()
    if function_groups is None:
        function_groups = list(
            general.MULTI_TENANT_FUNCTION_GROUPS
            if multi_tenant
            else general.DEFAULT_FUNCTION_GROUPS
        )

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

    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise mysqlsh.Error(
            "Give both --sslCertfile and --sslKeyfile to serve HTTPS, or neither."
        )
    if ssl_certfile and transport != general.TRANSPORT_STREAMABLE_HTTP:
        raise mysqlsh.Error("--sslCertfile and --sslKeyfile only apply to --transport=streamable-http.")

    if max_connections is not None:
        max_connections = int(max_connections)
        if max_connections < 1:
            raise mysqlsh.Error("max_connections must be at least 1.")
        general.MAX_CONNECTIONS_TOTAL = max_connections

    if multi_tenant:
        _check_multi_tenant(transport, function_groups, gui)

    # Disable interactive mode so the wrapped msm functions return their
    # results instead of prompting for input.
    mysqlsh.globals.shell.options.useWizards = False

    # Recorded before anything is served: the transport decides whether the
    # database connections are bound to the client that opened them and closed
    # when they fall idle (see mcp_plugin.lib.db_functions).
    general.set_active_transport(transport)

    # Recorded before the tools are built, because it decides which of them
    # exist: the connection-management tools are served in GUI mode only, and a
    # server cannot change what it advertises once a client has asked.
    general.set_gui_mode(gui)

    # The same for multi-tenant mode: it decides who may call a tool at all.
    general.set_multi_tenant(multi_tenant)

    auth = None
    if multi_tenant:
        from mcp_plugin.lib import auth as auth_module
        from mcp_plugin.lib import oauth_config

        if public_url:
            public_url = oauth_config.normalize_public_url(public_url)
        auth = auth_module.build_auth(public_url=public_url)
        if auth.public_url:
            # The name clients dial the server by, which a proxy in front of it
            # makes impossible to derive from the bind address.
            allowed_hosts = list(allowed_hosts) + [
                oauth_config.public_url_host(auth.public_url)
            ]

    mcp_server = build_mcp_server(
        function_groups=function_groups,
        auth=auth,
        tool_name_separator=config.get_tool_name_separator(),
    )

    if transport == general.TRANSPORT_STDIO:
        _serve_stdio(mcp_server)
    else:
        if multi_tenant:
            _warn_if_tokens_travel_in_clear(host, port, bool(ssl_certfile))
        else:
            _warn_if_reachable_from_the_network(host, port)
        _warn_if_gui_mode_over_http(gui, host, port)

        # The reaper that closes idle sessions and drops expired connections
        # belongs to the server, and this is where a server begins and ends. It
        # used to be started by the first db.connect, which made a thread nobody
        # stopped the side effect of a tool call.
        provider = auth.provider if auth is not None else None
        if provider is not None:
            # The built-in authorization server: its grants' login connections
            # are looked up through it, and its sweeper ends expired grants.
            provider.activate()
            provider.start_sweeper()

        db_functions.start_connection_reaper()
        try:
            _serve_streamable_http(
                mcp_server,
                host,
                port,
                allowed_hosts,
                ssl_certfile=ssl_certfile,
                ssl_keyfile=ssl_keyfile,
                auth=auth,
            )
        finally:
            db_functions.stop_connection_reaper()
            if provider is not None:
                provider.stop_sweeper()
                provider.deactivate()


def _check_multi_tenant(transport: str, function_groups, gui: bool) -> None:
    """Refuses to start a multi-tenant server in a way it cannot serve.

    Args:
        transport (str): The transport asked for.
        function_groups (list): The function groups asked for.
        gui (bool): Whether GUI mode was asked for.

    Returns:
        None

    Raises:
        mysqlsh.Error: If anything asked for is not available to tenants, or
            there is nobody to serve.
    """
    if transport != general.TRANSPORT_STREAMABLE_HTTP:
        raise mysqlsh.Error(
            "This server is configured for multi-tenant mode, which serves "
            "authenticated users over HTTP only: stdio has no request to carry "
            "a user's credentials. Use --transport=streamable-http, or turn "
            "multi-tenant mode off with mcp setup --multiTenant=false."
        )

    if gui:
        raise mysqlsh.Error(
            "--gui cannot be used in multi-tenant mode: it hands the client "
            "every local file and the connection list, which is for a single "
            "user's own editor."
        )

    refused = [
        group
        for group in function_groups
        if group not in general.MULTI_TENANT_FUNCTION_GROUPS
    ]
    if refused:
        raise mysqlsh.Error(
            f"The function group(s) {', '.join(refused)} are not available in "
            "multi-tenant mode, as they run local servers and long jobs on this "
            "machine. Available groups: "
            f"{', '.join(general.MULTI_TENANT_FUNCTION_GROUPS)}."
        )

    tenants.require_secret_groups()

    users = tenants.read_users()
    if not any(
        not record.get("disabled", False) for record in users.values()
    ) and not _users_are_created_at_sign_in():
        raise mysqlsh.Error(
            "Multi-tenant mode is on, but there is no enabled user to serve. "
            "Add one with mcp setup --addUser."
        )


def _users_are_created_at_sign_in() -> bool:
    """Returns whether the OAuth mode served creates users when they sign in.

    Such a server has somebody to serve before ``users.json`` has anyone in
    it: the first sign-in, which needs the server running, adds the first user.
    """
    from mcp_plugin.lib import oauth_config

    mode = oauth_config.get_mode()
    if mode == oauth_config.OAUTH_MODE_NONE:
        return False

    provision = oauth_config.get_oauth_settings()[mode].get("autoProvision") or {}

    return bool(provision.get("enabled"))


def _warn_if_tokens_travel_in_clear(host: str, port: int, tls: bool) -> None:
    """Warns when a multi-tenant server would take API keys over plain HTTP.

    Every request carries the user's bearer token, so a server reachable from
    other machines without TLS hands those tokens to anyone on the path. A
    warning and not a refusal: TLS is often terminated by a reverse proxy in
    front of the server, which this cannot see.

    Args:
        host (str): The host the server is about to bind to.
        port (int): The port the server is about to listen on.
        tls (bool): Whether the server serves HTTPS itself.

    Returns:
        None
    """
    if tls or general.is_loopback_host(host):
        return

    print(
        f"\nWARNING: the multi-tenant MariaDB MCP server is about to listen on "
        f"{host}:{port} over plain HTTP.\n"
        "         Every request carries a user's API key or access token, which "
        "anyone on the network\n"
        "         path can read. Serve HTTPS with --sslCertfile and --sslKeyfile, "
        "or put a TLS-terminating\n"
        "         reverse proxy in front of the server.\n",
        file=sys.stderr,
        flush=True,
    )


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


def _warn_if_gui_mode_over_http(gui: bool, host: str, port: int) -> None:
    """Warns when GUI mode is being served to anything but a local extension.

    GUI mode is a statement about who the client is: the MariaDB VS Code
    extension, which owns the shell process it started and talks to it over
    stdio. Served over HTTP that assumption is simply not checked - the server
    has no authentication (see :func:`_warn_if_reachable_from_the_network`), so
    every client that reaches the port gets what the extension was meant to
    get: read and write access to every file this user can read and write, and
    the ability to add connections to the list an MCP client may open.

    It is a warning and not a refusal, because there are legitimate reasons to
    serve a GUI-mode server over loopback - driving it from a tool that cannot
    speak stdio, or watching the traffic while developing the extension - and
    refusing would take those away to prevent a configuration nobody arrives at
    by accident. Written to stderr, like the bind warning above it.

    Args:
        gui (bool): Whether GUI mode was asked for.
        host (str): The host the server is about to bind to.
        port (int): The port the server is about to listen on.

    Returns:
        None
    """
    if not gui:
        return

    print(
        f"\nWARNING: the MariaDB MCP server is about to serve --gui over HTTP "
        f"on {host}:{port}.\n"
        "         --gui is meant for the MariaDB VS Code extension, which "
        "speaks to the server over\n"
        "         stdio and owns the process. Over HTTP there is NO "
        "AUTHENTICATION, so every client\n"
        "         that can reach this port gets full access to this user's "
        "files and can add\n"
        "         connections to the list any MCP client may open. Use "
        "--transport=stdio unless you\n"
        "         mean exactly this.\n",
        file=sys.stderr,
        flush=True,
    )


def _serve_streamable_http(
    mcp_server,
    host: str,
    port: int,
    allowed_hosts=(),
    ssl_certfile=None,
    ssl_keyfile=None,
    auth=None,
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
        ssl_certfile (str): The certificate to serve HTTPS with, or None.
        ssl_keyfile (str): Its private key, or None.
        auth: The :class:`mcp_plugin.lib.auth.AuthBundle` the server
            authenticates with, whose routes and middleware are added (see
            :func:`mcp_plugin.lib.auth.customize_app`) and whose refused
            tokens are throttled (see
            :class:`mcp_plugin.lib.auth.AuthFailureThrottle`), or None.

    Returns:
        None
    """
    import anyio
    import uvicorn

    starlette_app = mcp_server.streamable_http_app(
        host=host,
        transport_security=_transport_security_settings(host, port, allowed_hosts),
    )

    app = starlette_app
    if auth is not None:
        from mcp_plugin.lib.auth import AuthFailureThrottle, customize_app

        app = AuthFailureThrottle(customize_app(starlette_app, auth))

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=mcp_server.settings.log_level.lower(),
        proxy_headers=False,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
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
