# Migration Plugin for MariaDB Shell

This folder contains the code for the Migration Plugin. It is part of the [MariaDB Shell Plugins](../README.md) repository.

The migration plugin provides the MariaDB Shell backend for planning, validating,
and running MySQL database migrations to MySQL HeatWave on Oracle Cloud
Infrastructure.

## Opening the Migration Assistant

You do not open this plugin directly. Use one of these MySQL client tools:

- Install the **MariaDB Shell for VS Code** extension from the Visual Studio Code
  Extensions view, then open the MySQL HeatWave Migration Assistant from the
  extension.
- Open **MySQL Workbench** and use its migration workflow to start a MySQL
  migration.

For local development, open the repository workspace file
`MySQLShellPluginDevelopment.code-workspace` in Visual Studio Code.

## Required OCI Policies

Before running a migration, make sure the OCI user belongs to a group with the
following policies. Replace `GroupName` with the OCI group name and
`ParentCompartmentName` with the parent compartment selected for Migration
Assistant resources. If the parent is the root compartment, use `in tenancy`
instead of `in compartment ParentCompartmentName` for the compartment-scoped
statements.

The in-app OCI sign-in/bootstrap uploads and lists the signed-in user's own API
keys. OCI allows users to manage their own API keys without an administrator
policy, so no extra `manage users` policy is normally required for that path.

```text
# Required. Needed to read tenancy metadata and region subscription/home-region information.
Allow group GroupName to inspect tenancies in tenancy

# Required for compartment picker/discovery and availability-domain lookup.
Allow group GroupName to inspect compartments in tenancy

# Required for planning/discovery of MySQL shapes, configurations, DB Systems, and HeatWave state.
Allow group GroupName to inspect mysql-family in tenancy

# Required for planning/discovery of compute images, shapes, and existing jump-host instances.
# "inspect instance-family" is not sufficient because Migration Assistant lists instances.
Allow group GroupName to read instance-family in tenancy

# Required when Migration Assistant creates the MySQL/Networks child compartments under the selected parent.
Allow group GroupName to manage compartments in compartment ParentCompartmentName

# Required to create, update, and roll back the target MySQL DB System, configuration, HA, HeatWave cluster, and replication channel.
Allow group GroupName to manage mysql-family in compartment ParentCompartmentName

# Required to create and clean up the temporary compute jump host.
Allow group GroupName to manage instance-family in compartment ParentCompartmentName

# Required to clean up the temporary compute jump host when OCI must detach attached boot or block volumes.
Allow group GroupName to use volume-family in compartment ParentCompartmentName

# Required to create and clean up the migration bucket, objects, multipart uploads, and pre-authenticated requests.
Allow group GroupName to manage object-family in compartment ParentCompartmentName

# Required to create or update VCNs, subnets, gateways, route rules, and VNIC attachments.
# If all network resources and rules are pre-created, this can be reduced to "use virtual-network-family".
Allow group GroupName to manage virtual-network-family in compartment ParentCompartmentName
```

## Contributing to MySQL Migration Plugin

No installation is necessary for this plugin, besides setting up Visual Studio Code to work on the code.

## Running the Tests

To run the tests, MariaDB Shell must be installed and on your `PATH`.

### Installing the Python Requirements

From the repository root, run the following command in the terminal.

```bash
mariadb-shell --pym pip install -r migration_plugin/requirements.txt
```

### Running the Tests

The tests can be executed by running the `run-pytest` script in the NPM SCRIPTS section of the VS Code sidebar.

To run the tests from the terminal, change into the `migration_plugin` folder and run the following command.

```bash
mariadb-shell --log-level=debug3 --verbose=4 --py -f run_tests.py
```

To run a specific test, use the `-k` option.

```bash
mariadb-shell --log-level=debug3 --verbose=4 --py -f run_tests.py -k test_plugin_version
```

Copyright &copy; 2025, 2026, Oracle and/or its affiliates.
