# MariaDB REST Service Plugin for MariaDB Shell

This folder contains the code for the MariaDB REST Service (short: MRS) Plugin. It is part of the [MariaDB Shell Plugins](../README.md) repository.

The MRS documentation, the ANTLR reference grammar of REST SQL and the tools
that keep the two in step live in the MariaDB Shell repository: the GitBook
source in `docs-ref/content/mariadb-rest-service/`, the grammar in
`modules/mrs/antlr_grammar/`. REST SQL itself is implemented by the shell's
built-in `mrs` module; this plugin adds the SDK generation, project, service and
content set functions.

# Contributing to MariaDB REST Service Plugin

No installation is necessary for this plugin, beside the setup of Visual Studio Code to be able to work on the code.

## Testing

Since this is a MariaDB Shell plugin, the tests should run as part of a MariaDB Shell session. The test script should be executed within the `mrs_plugin` directory and the required testing dependencies should be installed as follows:

```sh
$ cd mrs_plugin
$ mariadb-shell --pym pip install --user -r requirements.txt
$ mariadb-shell --pym pip install --user -r sdk/python/requirements.txt
```

After the dependencies are installed, the test script can execute as follows:

```sh
$ mariadb-shell --py -f run_tests.py
```

The suite deploys its own MariaDB sandbox on a free port and removes it again
at the end, so no database server needs to be set up; a `mariadbd` binary has
to be on the `PATH`, though.

To run a single test or test suite, the script provides a `-k` option that allows to specify a test name or a file name.

```sh
$ mariadb-shell --py -f run_tests.py -k test_sdk
```

## Visual Studio Code Settings

Include the following settings in your VS Code settings.json file in order to allow the Python linter find the referenced packages.

```json
{
    "python.analysis.extraPaths": [
        "/usr/local/mariadb-shell/lib/mariadb-shell/python-packages/",
        "${workspaceFolder}\\plugins\\rds_plugin",
        "/usr/local/mariadb-shell/lib/mariadb-shell/lib/python3.9/site-packages"
    ],
    "python.autoComplete.extraPaths": [
        "/usr/local/mariadb-shell/lib/mariadb-shell/python-packages/",
        "/usr/local/mariadb-shell/lib/mariadb-shell/lib/python3.9/site-packages",
        "${workspaceFolder}\\plugins\\rds_plugin"
    ]
}
```

Copyright &copy; 2020, 2026, Oracle and/or its affiliates.
Copyright (c) 2026, MariaDB plc.
