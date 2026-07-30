<!-- Copyright (c) 2026, Oracle and/or its affiliates.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License, version 2.0,
as published by the Free Software Foundation.

This program is designed to work with certain software (including
but not limited to OpenSSL) that is licensed under separate terms, as
designated in a particular file or component or in included license
documentation.  The authors of MySQL hereby grant you an additional
permission to link the program and your derivative works with the
separately licensed software that they have either included with
the program or referenced in the documentation.

This program is distributed in the hope that it will be useful,  but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
the GNU General Public License, version 2.0, for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA -->

<!-- cSpell:ignore pandoc -->

# Prerequisites

The following pre-requisites need to be satisfied to execute the E2E tests by any of the ways described below:

- The binary of the MySQL Server to be used in PATH
- The binary of the MariaDB Shell to be used in PATH
- The Google Chrome to be used in PATH
- The Google Chromedriver to be used in PATH
- Npm available in PATH
- Have the Shell GUI Frontend built

# Quick Launch (Single Test)

Possible by customizing the .test.env.json file (deeper details at the bottom)

Using a custom MySQL Server: set MYSQL_PORT, DBUSERNAME1 and DBUSERNAME1PWD to the ones for your server.

Using custom account for OCI tests: set MYSQLSH_OCI_CONFIG_FILE and MYSQLSH_OCI_CONFIG_PROFILE to the right values.

Using custom account for HeatWave tests: set HWHOSTNAME and HWPASSWORD to the right values.

## Execution and Debugging

Triggering the execution of a given test or test suite is very simple using the **vitest** extension in the Visual Studio Code.

With the extension installed, just make sure that the required MySQL Server and Backend Server are running  and switch to
the Testing (VSCode Side Bar), to see the list of tests in the suite where with a single click, you can execute and
even debug any of the tests.

For additional capabilities on this mode, please refer to the **vitest** extension documentation.


# Quick Launch (Full Test Suite)

We have a script that will automatically handle:

- Setup and launch of the required Shell GUI Backend servers
- Setup and launch of the required MySQL Servers
- Execute the entire E2E test quite
- Tear down the Shell GUI Backend Servers and MySQL Servers

Simply execute the following either on the terminal

```bash
$ npm run e2e-tests-setup-and-run
```

Or hit plain on it in the NPM SCRIPTS section for the Shell GUI Frontend at the sidebar.

## Execute a specific test

The tests files grabbed for execution are defined through the `test.include` setting at the `e2e.vitest.config.ts` file at the
root of the Shell GUI Frontend project, simply update it from `src/tests/e2e/tests/**/ui-*.ts` to the specific file you
need to execute, i.e. `src/tests/e2e/tests/**/ui-shell.ts`.

Or do it graphically as explained before.

# Developer Launch

If you are either updating a test or adding new tests due to changes in the Shell GUI Frontend sources, using the `Quick Launch`
method may be a bad idea, as on every execution, it will fully setup the required test environment, execute the test(s) and then
teardown the environment.

If this is your case, the three operations have been split so you can i.e. execute the setup once, run tests as many times as you need
and finally teardown the environment once, when you are done.

For this, you can execute the following commands either on the terminal, or by hitting `play` in the corresponding item on the NPM SCRIPTS section
for the Shell GUI Frontend project.

```bash
$ npm run e2e-tests-setup
```

```bash
$ npm run e2e-tests-execute
```

```bash
$ npm run e2e-tests-teardown
```

Here, you can also update your configuration to run a specific test and do the execute step as may times as needed to get the test to work as expected.

# More Customization

The execution of the E2E tests relies on the existence of a configuration file named `src/tests/.env.test.json`, both the **e2e-tests-setup** script or the **e2e-tests-setup-and-run** would automatically create this file from `src/tests/.env.test.json.in` which contains the required configuration to execute the
E2E test suite. Some notes on this file:

- The configured MySQL Users and Passwords are sample local users deployed when the testing MySQL Server sandboxes are deployed, no changes should be done there.
- The configured MYSQL_PORT and MYSQL_REST_PORT should not be modified.
- The HWHOSTNAME and HWPASSWORD options are to define credentials to a Heatwave Enabled MySQL Server to execute the HeatWave related tests, if they are left empty, such tests will not be executed.
- The MYSQLSH_OCI_CONFIG_FILE and MYSQLSH_OCI_CONFIG_PROFILE options are to define an OCI Profile file and profile to be used on the OCI related testing
  if they are missing, the tests will be automatically skipped.
- The TOKEN string, is used to authenticate a connection from the Shell GUI Frontend to the Shell GUI Backend servers, can be configured to something different, but is not really required.
- The SHELL_UI_HOSTNAME is used for the tests to find out the URL where the target BE servers are running, since the BE servers run in localhost, this value should not be modified.
- E2E_DEBUG is defined, so it causes you can see the chrome browser being opened and executing the tasks defined on a given test, if you need to run the tests in HEADLESS mode, simply delete this configuration.
- The MAX_WORKERS is used to define the maximun number of worker threads to be used by vitest to run the test suite, note that this is only used when executing the tests through `e2e-tests-setup-and-run` and `e2e-tests-execute`


The methods given above are a simplified way to execute the E2E test suite but there are many other options available, for additional information
refer to the **vitest** framework documentation and the **vitest** extension documentation.


Copyright &copy; 2020, 2024, Oracle and/or its affiliates.
