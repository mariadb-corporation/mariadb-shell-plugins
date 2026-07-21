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

"""MCP tools for working with local MariaDB/MySQL sandbox instances.

These tools wrap the shell's ``sandbox`` global object, which deploys and
manages self-contained server instances under ``<sandboxDir>/<port>`` for local
testing and development.

Sandbox instances are only meant to run on the local machine and are not
accessible from external networks.
"""

# cSpell:ignore mysqlsh MariaDB fastmcp sandboxlib mariadbd openssl

from typing import Optional

import mysqlsh

from mcp_plugin.lib import config


def register_sandbox_tools(server) -> None:
    """Registers the sandbox management tools on the given server.

    Args:
        server: The FastMCP server instance to register the tools on.

    Returns:
        None
    """
    from mysqlsh.globals import sandbox

    def _options(**pairs) -> dict:
        """Builds an options dict, dropping keys whose value is None."""
        return {key: value for key, value in pairs.items() if value is not None}

    def _require_allowed_path(path) -> None:
        """Raises unless the given path is within an allowed directory.

        A value of None is left to the sandbox's own default directory handling.
        """
        if path is None:
            return
        if not config.is_path_allowed(path):
            raise mysqlsh.Error(
                f"Access to path '{path}' is not allowed. Add it (or a parent "
                "directory) to the allowed paths with mcp.setup."
            )

    @server.tool(name="sandbox.deploy")
    def deploy(
        port: int,
        password: Optional[str] = None,
        sandbox_dir: Optional[str] = None,
        allow_root_from: Optional[str] = None,
        server_id: Optional[int] = None,
        ssl: bool = False,
        openssl_path: Optional[str] = None,
        mariadbd_path: Optional[str] = None,
        mariadbd_options: Optional[list] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Deploys a new MariaDB or MySQL sandbox instance on localhost.

        Deploys a plain standalone instance using the server found on the PATH
        (or at mariadbd_path). The server is started, the root password is set
        and the instance is left running. SSL/TLS is disabled by default; pass
        ssl=True to enable it.

        Args:
            port: The port the new instance will listen on.
            password: Password for the root user on the new instance.
            sandbox_dir: Path where the new instance will be deployed.
            allow_root_from: Host pattern for a remote root account to create.
                Defaults to '%'. Set to an empty string to skip creating it.
            server_id: server_id value for the instance.
            ssl: Whether to generate SSL certificates and enable TLS. Defaults
                to False (unlike the shell's sandbox default of True) to avoid
                depending on openssl for local test instances.
            openssl_path: Path to the openssl executable or its directory.
            mariadbd_path: Path to the mariadbd/mysqld binary or its
                installation directory.
            mariadbd_options: Additional server options for the [mysqld]
                section, as 'option=value' strings.
            timeout: Seconds to wait for the instance to start. Defaults to 60.

        Returns:
            A message confirming the deployment.
        """
        _require_allowed_path(sandbox_dir)
        sandbox.deploy(
            port,
            _options(
                password=password,
                sandboxDir=sandbox_dir,
                allowRootFrom=allow_root_from,
                serverId=server_id,
                ssl=ssl,
                opensslPath=openssl_path,
                mariadbdPath=mariadbd_path,
                mariadbdOptions=mariadbd_options,
                timeout=timeout,
            ),
        )
        return f"Sandbox instance deployed and started on port {port}."

    @server.tool(name="sandbox.start")
    def start(
        port: int,
        sandbox_dir: Optional[str] = None,
        mariadbd_path: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Starts an existing MariaDB/MySQL sandbox instance on localhost.

        Args:
            port: The port of the instance to start.
            sandbox_dir: Path where the instance is located.
            mariadbd_path: Path to the mariadbd/mysqld binary or its
                installation directory.
            timeout: Seconds to wait for the instance to start. Defaults to 60.

        Returns:
            A message confirming the instance was started.
        """
        _require_allowed_path(sandbox_dir)
        sandbox.start(
            port,
            _options(
                sandboxDir=sandbox_dir,
                mariadbdPath=mariadbd_path,
                timeout=timeout,
            ),
        )
        return f"Sandbox instance on port {port} started."

    @server.tool(name="sandbox.stop")
    def stop(
        port: int,
        sandbox_dir: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Gracefully stops a running sandbox instance on localhost.

        Args:
            port: The port of the instance to stop.
            sandbox_dir: Path where the instance is located.
            password: Root password (used on Windows to request the shutdown).
            timeout: Seconds to wait for the instance to stop. Defaults to 60.

        Returns:
            A message confirming the instance was stopped.
        """
        _require_allowed_path(sandbox_dir)
        sandbox.stop(
            port,
            _options(
                sandboxDir=sandbox_dir,
                password=password,
                timeout=timeout,
            ),
        )
        return f"Sandbox instance on port {port} stopped."

    @server.tool(name="sandbox.kill")
    def kill(port: int, sandbox_dir: Optional[str] = None) -> str:
        """Forcefully kills the process of a running sandbox instance.

        Use sandbox.stop for a graceful shutdown.

        Args:
            port: The port of the instance to kill.
            sandbox_dir: Path where the instance is located.

        Returns:
            A message confirming the instance was killed.
        """
        _require_allowed_path(sandbox_dir)
        sandbox.kill(port, _options(sandboxDir=sandbox_dir))
        return f"Sandbox instance on port {port} killed."

    @server.tool(name="sandbox.delete")
    def delete(port: int, sandbox_dir: Optional[str] = None) -> str:
        """Deletes an existing sandbox instance on localhost.

        The instance must be stopped before it can be deleted.

        Args:
            port: The port of the instance to delete.
            sandbox_dir: Path where the instance is located.

        Returns:
            A message confirming the instance was deleted.
        """
        _require_allowed_path(sandbox_dir)
        sandbox.delete(port, _options(sandboxDir=sandbox_dir))
        return f"Sandbox instance on port {port} deleted."

    @server.tool(name="sandbox.vendor")
    def vendor(
        port: int,
        sandbox_dir: Optional[str] = None,
        mariadbd_path: Optional[str] = None,
    ) -> Optional[str]:
        """Reports the server vendor ('MariaDB' or 'MySQL') of a sandbox.

        Args:
            port: The port of the existing sandbox to report the vendor of.
            sandbox_dir: Path where the instance is located.
            mariadbd_path: Path to the mariadbd/mysqld binary or its
                installation directory.

        Returns:
            "MariaDB", "MySQL", or None if the vendor cannot be determined.
        """
        _require_allowed_path(sandbox_dir)
        return sandbox.vendor(
            port,
            _options(sandboxDir=sandbox_dir, mariadbdPath=mariadbd_path),
        )

    @server.tool(name="sandbox.version")
    def version(
        port: int,
        sandbox_dir: Optional[str] = None,
        mariadbd_path: Optional[str] = None,
    ) -> Optional[str]:
        """Reports the server version of a sandbox.

        Args:
            port: The port of the existing sandbox to report the version of.
            sandbox_dir: Path where the instance is located.
            mariadbd_path: Path to the mariadbd/mysqld binary or its
                installation directory.

        Returns:
            The version as major.minor.patch, or None if it cannot be
            determined.
        """
        _require_allowed_path(sandbox_dir)
        return sandbox.version(
            port,
            _options(sandboxDir=sandbox_dir, mariadbdPath=mariadbd_path),
        )
