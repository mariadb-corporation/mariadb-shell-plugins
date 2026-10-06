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

"""The MariaDB MCP Server Plugin"""

# cSpell:ignore mysqlsh MariaDB

from mysqlsh.plugin_manager import plugin_function
import mcp_plugin.lib as lib


@plugin_function("mcp.info", shell=True, cli=True, web=True)
def info() -> str:
    """Returns basic information about this plugin.

    Returns:
        str
    """
    return (
        f"MariaDB MCP Server Plugin Version {lib.general.VERSION} PREVIEW\n"
        "Warning! For testing purposes only!"
    )


@plugin_function("mcp.version", shell=True, cli=True, web=True)
def version() -> str:
    """Returns the version number of the plugin.

    Returns:
        str
    """
    return lib.general.VERSION


@plugin_function("mcp.setup", shell=True, cli=True, web=False)
def setup(**options) -> None:
    """Configures the MariaDB MCP server, interactively or from options.

    Called without options this is an interactive walkthrough. On the first run
    it walks through adding connections and allowed paths; on subsequent runs it
    presents a menu to add or delete connections and paths and to install or
    remove the MySQL-to-MariaDB migration tooling.

    Called with any option it does exactly what the options say and asks
    nothing else, so it can run where there is no terminal - a provisioning
    script or a CI job. Every menu item has an option. Deletions are carried
    out before additions, and the migration tooling last, so passing both
    removeMigrator and installMigrator reinstalls it. Anything that fails stops
    the run, leaving what already succeeded in place and reported.

    For each connection the URI is stored normalized, the password is verified
    by opening a session unless noVerify is given, and the password is kept in
    the shell's secret store rather than in any file. The allowed directories go
    into a settings.json file in the plugin data directory. The migration
    tooling is downloaded into '~/.local/share/mariadb-migrator/<version>',
    given a virtual environment built with the interpreter this shell bundles,
    and wrapped at '~/.local/bin/mariadb-migrator' so it can be run by name; no
    system Python is required, and as a POSIX shell program it is not offered on
    Windows at all.

    In multi-tenant mode (multiTenant) the server serves several users, each
    authenticated with an API key and each with connections and allowed paths
    of their own. Users are added with addUser, which prints their API key, and
    are named in every other option by their id or by any of their identities,
    each being an email address, a user id of your choosing ('userId:<id>'), an
    OAuth identity ('oauth:<issuer>|<subject>') or a MariaDB account
    ('mariadb:<server>|<account>'). A user's secrets - their API key and their
    connection passwords - are kept in a secret group of their own. Groups keep
    users apart, they do not protect them from each other: run the server under
    an OS account of its own. In multi-tenant mode the connection and path
    options need user, naming whose they are.

    Args:
        **options (dict): Options saying what to configure.

    Keyword Args:
        add_connection (str): The URI of one connection to verify and store,
            for example user@host:3306. One per call, since each needs its own
            password. A URI carrying a password is refused: pass the password
            with one of the options below instead. Storing a connection that is
            already configured updates its password.
        password (str): The password for add_connection. Discouraged: a command
            line is visible to other processes and lands in shell history. One
            of the three password options at most.
        password_env (str): The NAME of an environment variable holding the
            password for add_connection, so that the password itself never
            appears in the command line. The variable must be set; an empty
            value is taken as an empty password.
        password_stdin (bool): Read the password for add_connection from the
            first line of stdin, for piping it in from a secret manager.
            Refused when stdin is a terminal, where it would wait for input
            nobody knows to type.
        no_verify (bool): Store the connection without opening a session to
            check it first, for configuring a server that is not up yet. With
            oauth_issuer, save the issuer without reading its configuration.
        delete_connections (str): Comma-separated URIs of connections to
            delete. Any spelling that names a configured connection works.
        add_paths (str): Comma-separated directories the MCP server may access.
            Each must already exist.
        delete_paths (str): Comma-separated directories to stop allowing.
        install_migrator (bool): Download the configured release of the
            MySQL-to-MariaDB migration tooling, build its virtual environment
            and install its wrapper.
        remove_migrator (bool): Remove every installed release of the migration
            tooling, and its wrapper.
        non_interactive (bool): Never prompt. A password that was not supplied
            is an error rather than a question, so an automated run fails
            instead of waiting.
        show (bool): Print the current configuration and do nothing else.
            Cannot be combined with the options that change something.
        json (bool): Print what show reports as JSON. Also prints the API keys
            of add_user, rotate_api_key and show_api_key as JSON, when those
            are the only options given.
        multi_tenant (bool): Turn multi-tenant mode on or off. Nothing is moved
            either way: the connections of each mode stay where they are.
        add_user (str): Add a user, known by the given comma-separated
            identities, and print their API key.
        name (str): The name to show for the user add_user adds.
        scopes (str): Comma-separated scopes the user add_user adds may be
            granted: mcp:db, mcp:msm. Defaults to both.
        remove_user (str): Comma-separated users to remove, together with
            their API keys and connections.
        user (str): The user add_connection, delete_connections, add_paths,
            delete_paths, add_identity, remove_identity and set_scopes work on,
            or the user show reports.
        add_identity (str): Comma-separated identities to add to user.
        remove_identity (str): Comma-separated identities to remove from user.
        disable_user (str): Comma-separated users to disable. A disabled user
            keeps everything and is refused from their next request on.
        enable_user (str): Comma-separated users to enable again.
        set_scopes (str): Comma-separated scopes user may be granted.
        rotate_api_key (str): Comma-separated users to issue a new API key to.
            Their previous key stops working at once.
        show_api_key (str): Comma-separated users whose API key to print.
        all_users (bool): Make show report every user with their connections
            and allowed paths, and the secret groups that belong to no user.
        purge_orphan_groups (bool): Delete the secret groups that belong to no
            user, as an interrupted remove_user leaves behind.
        set_default_role (str): The MariaDB role the sessions of user run
            under. Empty uses the account's own default role.

    Returns:
        None
    """
    lib.setup.run_setup(**options)


@plugin_function("mcp.setupOauth", shell=True, cli=True, web=False)
def setup_oauth(**options) -> None:
    """Configures how a multi-tenant MCP server takes OAuth2 tokens.

    Called without options this is an interactive menu. Called with options it
    does exactly what they say. API keys keep working in every mode; OAuth adds
    tokens issued by Keycloak (mode keycloak) or by this server itself, which
    then signs users in with their MariaDB account (mode builtin). Tokens are
    only accepted when issued for the server's public URL, which every mode but
    none needs.

    Clients of the built-in server are registered here too - a server-side
    gateway such as Arcade as a confidential client, whose secret is printed
    and can be shown again, and whose redirect URI can be set once the gateway
    has shown it.

    Args:
        **options (dict): Options saying what to configure.

    Keyword Args:
        public_url (str): The URL clients reach the MCP endpoint at, such as
            https://mcp.example.com/mcp, which tokens are issued for. Plain
            http is only accepted on loopback. An empty value clears it.
        mode (str): How OAuth2 tokens are taken besides API keys. One of
            none, keycloak or builtin.
        issuer (str): The Keycloak realm's issuer URL, such as
            https://kc.example.com/realms/mariadb. Checked by reading its
            OpenID configuration unless no_verify is given.
        no_verify (bool): Save issuer without reading its configuration.
        verification (str): How Keycloak tokens are checked, jwt (their
            signature) or introspection (asking Keycloak, cached for 30s).
        introspection_client_id (str): The Keycloak client introspection
            authenticates as.
        introspection_secret_env (str): The NAME of an environment variable
            holding that client's secret.
        client_ids (str): Comma-separated Keycloak clients whose tokens are
            accepted. Empty accepts any.
        link_by_verified_email (bool): Link a Keycloak sign-in to the user
            whose email identity is the token's verified email.
        required_realm_role (str): The Keycloak realm role a token needs for
            its user to be created at sign-in.
        add_login_server (str): Comma-separated MariaDB servers users sign in
            to the built-in server against, such as
            mariadb://db.example.com:3306.
        remove_login_server (str): Comma-separated login servers to remove.
        required_role (str): The MariaDB role an account needs to sign in to
            the built-in server. Empty allows any account.
        grant_max_lifetime (int): How long a sign-in to the built-in server
            lasts, in seconds. Defaults to 90 days.
        grant_idle_timeout (int): How long a sign-in lasts without a token
            refresh, in seconds. 0 turns it off, the default.
        access_token_lifetime (int): How long an access token of the built-in
            server is valid, in seconds. Defaults to 3600.
        refresh_grace_period (int): How long a refresh token just replaced is
            still answered with the same new tokens, in seconds, for a client
            that refreshed twice in a race. Defaults to 30.
        login_connection_store (str): Where a sign-in's MariaDB connection is
            kept for as long as it lasts, secret-store or memory.
        allowed_client_networks (str): Comma-separated networks the MCP
            endpoint and the token endpoints accept requests from. Empty
            accepts any.
        dynamic_client_registration (bool): Let clients register themselves
            with the built-in server.
        cimd (bool): Accept clients that name an https Client ID Metadata
            Document as their client id.
        auto_provision (bool): Create a user at their first sign-in, in the
            configured mode.
        default_scopes (str): Comma-separated scopes users created at sign-in
            may be granted.
        add_client (str): Register a client of the built-in server under the
            given name, and print its id and, if confidential, its secret.
        confidential (bool): Whether the client add_client registers
            authenticates with a secret.
        redirect_uris (str): Comma-separated redirect URIs for add_client or
            set_client_redirect_uris.
        set_client_redirect_uris (str): The client whose redirect URIs to set
            to redirect_uris.
        set_client_allowed_roles (str): The client whose allowed roles to set
            to roles. A sign-in through it is refused unless the role the
            session would run under is one of them.
        roles (str): Comma-separated MariaDB roles for
            set_client_allowed_roles. Empty allows any.
        show_client_secret (str): The client whose secret to print.
        rotate_client_secret (str): The client to give a new secret.
        remove_client (str): The client to remove, ending every sign-in
            through it.
        list_clients (bool): Print the registered clients.
        rotate_signing_key (bool): Replace the key the built-in server signs
            access tokens with. Every access token issued so far stops working.
        revoke_tokens (str): Comma-separated users whose OAuth tokens to
            revoke, ending their sign-ins to the built-in server.
        show (bool): Print the OAuth configuration and do nothing else.
        json (bool): Print what show reports as JSON. Also prints the client
            ids and secrets of add_client, show_client_secret,
            rotate_client_secret and list_clients as JSON, when those are the
            only options given.

    Returns:
        None
    """
    lib.setup_oauth.run_setup_oauth(**options)


@plugin_function("mcp.setupKeycloakRealm", shell=True, cli=True, web=False)
def setup_keycloak_realm(**options) -> None:
    """Prepares a Keycloak realm to issue tokens for this MCP server.

    Run once by a Keycloak administrator. Anything not given as an option is
    asked for, the administrator password with a password prompt. Safe to run
    again: what exists already is left alone. In the realm it creates the
    client scopes mcp:db and mcp:msm, each putting this server's public URL
    into the token's audience, the realm role mcp-user a user needs to be
    created at first sign-in, and a public PKCE client for MCP clients. It then
    points this server at the realm, unless configure_server is false.

    Args:
        **options (dict): Options saying what to prepare.

    Keyword Args:
        server (str): The Keycloak base URL, such as https://kc.example.com.
        realm (str): The realm to prepare.
        admin_realm (str): The realm the administrator signs in to. Defaults
            to master.
        admin_user (str): The administrator's user name.
        admin_password_env (str): The NAME of an environment variable holding
            the administrator's password.
        admin_password (str): The administrator's password. Discouraged: a
            command line is visible to other processes. Leave it out to be
            asked.
        mcp_url (str): This server's public URL, which tokens are issued for.
            Defaults to the one set with mcp setup-oauth --publicUrl.
        client_id (str): The id of the client created for MCP clients.
            Defaults to mariadb-mcp.
        direct_grant (bool): Also allow the password grant on that client, for
            the opt-in live test only - never on a production client.
        grant_realm_role_to (str): Comma-separated user names or emails to
            give the realm role mcp-user.
        configure_server (bool): Also point this server at the realm (mode
            keycloak and its issuer). Defaults to true.
        non_interactive (bool): Never prompt: something not given is an error.

    Returns:
        None
    """
    lib.setup_keycloak.run_setup_keycloak_realm(**options)
