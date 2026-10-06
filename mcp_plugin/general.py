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
            check it first, for configuring a server that is not up yet.
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

    Returns:
        None
    """
    lib.setup.run_setup(**options)
