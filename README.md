# MariaDB Shell Plugins

The MariaDB Shell Plugins Repository is a collection of plugins for the [MariaDB Shell](https://github.com/mariadb-corporation/mariadb-shell). It covers currently these plugins:

- **MCP plugin**: implements [MariaDB MCP Server](mcp_plugin/readme.md)
- **MRS plugin**: implements [MariaDB REST Service](mrs_plugin/readme.md) support
- **MSM plugin**: implements [MariaDB Schema Management operations](msm_plugin/readme.md)

## Installation

Read the individual project readme files for more details, how to contribute and other information.

The following plugins are installed by copying or symlinking the folders into the MariaDB Shell Plugins directory:

- mcp_plugin
- mrs_plugin
- msm_plugin

The plugins location for the MariaDB Shell depends on the target platform:

- Windows: %appdata%\MariaDB\mariadb-shell\plugins
- Others: ~/.mariadb-shell/plugins

## Documentation

For full documentation on the MariaDB Shell, see the `MARIADB_PORT.md` and `man/mariadb-shell.1` files in the [mariadb-shell](https://github.com/mariadb-corporation/mariadb-shell) repository. For MySQL Server reference material, see: https://dev.mysql.com/doc/refman/en/

## Contributing

This project welcomes contributions from the community. Before submitting a pull request, please [review our contribution guide](./CONTRIBUTING.md)

## License

License information can be found in the LICENSE file.

This distribution may include materials developed by third parties. For license and attribution notices for these materials, please refer to the LICENSE file.

For the source of the MariaDB Shell itself, see: https://github.com/mariadb-corporation/mariadb-shell

Copyright &copy; 2022, 2026, Oracle and/or its affiliates.
Copyright &copy; 2026, MariaDB plc.
