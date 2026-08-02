# MariaDB Shell Plugins

The MariaDB Shell Plugins Repository is a collection of plugins for the [MariaDB Shell](https://github.com/mariadb-corporation/mariadb-shell). It covers currently these plugins:

- **GUI plugin**: provides backend functionality for the [MariaDB Shell GUI](gui/frontend/readme.md) application.
- **MDS plugin**: implements [MySQL Database Services](mds_plugin/readme.md) and Oracle Cloud Infrastructure support
- **MRS plugin**: implements [MySQL REST Service](mrs_plugin/readme.md) support
- **MSM plugin**: implements [MySQL Schema Management operations](msm_plugin/readme.md)
- **Migration plugin**: implements [MySQL Migration operations](migration_plugin/README.md)

## Installation

The GUI plugin backend and the other plugins can all be used on their own via MariaDB Shell, but together they power the [MariaDB Shell GUI](gui/frontend/readme.md) and the [MariaDB Shell for VS Code](gui/extension/readme.md). Read the individual project readme files for more details, how to contribute and other information.

The following plugins are installed by copying the folders into the MariaDB Shell Plugins directory:

- mds_plugin
- mrs_plugin
- msm_plugin
- util_plugin
- migration_plugin

The plugins location for the MariaDB Shell depends on the target platform:

- Windows: %appdata%\MariaDB\mariadb-shell\plugins
- Others: ~/.mariadb-shell/plugins

For instructions about how to build and install the gui_plugin refer to the MariaDB Shell GUI [readme.md](gui/frontend/readme.md).

## Documentation

For full documentation on the MariaDB Shell, see the `MARIADB_PORT.md` and `man/mariadb-shell.1` files in the [mariadb-shell](https://github.com/mariadb-corporation/mariadb-shell) repository. For MySQL Server reference material, see: https://dev.mysql.com/doc/refman/en/


## Contributing

This project welcomes contributions from the community. Before submitting a pull request, please [review our contribution guide](./CONTRIBUTING.md)


## Security

Please consult the [security guide](./SECURITY.md) for our responsible security vulnerability disclosure process


## License

License information can be found in the LICENSE.txt file.

This distribution may include materials developed by third parties. For license and attribution notices for these materials, please refer to the LICENSE file.

For the source of the MariaDB Shell itself, see: https://github.com/mariadb-corporation/mariadb-shell

Copyright &copy; 2022, 2026, Oracle and/or its affiliates.


