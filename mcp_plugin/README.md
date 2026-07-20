# MariaDB MCP Server Plugin

This folder contains the code for the **MariaDB MCP Server Plugin**. It hosts the
Model Context Protocol (MCP) server functionality for the MariaDB AI Plugins,
exposing their capabilities to MCP-compatible clients such as AI assistants and
agent frameworks.

The plugin registers the global `mcp` object in the MariaDB/MySQL Shell and runs a
[FastMCP](https://github.com/modelcontextprotocol/python-sdk) server in the
foreground. It supports two transports, selected with the `transport` option:

- **`streamable-http`** (default): served over HTTP on the configured `host`/`port`.
- **`stdio`**: communicates over stdin/stdout (`host`/`port` are ignored). Its
  lifetime is driven by the client; it exits when stdin is closed.

The server is meant to be launched from the command line. Serving blocks for the
lifetime of the server; the shell's interactive mode is disabled while it runs so
the wrapped functions return their results instead of prompting for input.

## Shell functions

| Function | Description |
| --- | --- |
| `mcp.info()` | Returns basic information about the plugin. |
| `mcp.version()` | Returns the version number of the plugin. |
| `mcp.setup()` | Interactively configures the allowed connections and paths. |
| `mcp.start_server(options)` | Starts the MCP server; blocks until terminated (`host`, `port` options). |

## Configuration (`mcp.setup`)

Before starting the server, run `mcp.setup` from an interactive shell to configure
what the MCP server is allowed to access:

```bash
mariadb-shell --py -e "mcp.setup()"
```

- **Connections**: enter a MariaDB connection URI (e.g. `user@host:3306`). The
  password is prompted for and the connection is verified with `shell.open_session()`
  before the password is stored in the shell secret store under the key
  `MCP:Connection:<uri>`. The `db.*` tools only allow the connections configured here.
- **Allowed paths**: choose the local directories the server may access (the current
  directory is suggested as the default, shown as a full path). These are stored in a
  `settings.json` file in the plugin data directory.

On the first run, `mcp.setup` walks through adding connections and then paths. On
subsequent runs it presents a menu to add or delete connections and paths.

## Exposed MCP tools

The tools are grouped into function groups that can be loaded independently via the
`function_groups` option of `mcp.start_server` (`db`, `msm`; defaults to all):

```bash
# expose only the database tools
mariadb-shell -- mcp start-server --function-groups=db
```

### Database connection tools (`db`)

Tools for working with the connections configured via `mcp.setup`. Sessions opened
with `db.connect` are cached in-process and identified by the returned UUID:

| MCP tool | Description |
| --- | --- |
| `db.list_connections` | Lists the configured connection URIs. |
| `db.connect` | Opens a configured connection (`shell.open_session()`) and returns a connection UUID. |
| `db.execute_sql` | Runs a SQL statement (with optional parameters) on a connection UUID. |
| `db.close` | Closes the connection for a UUID (`session.close()`). |

### Schema management tools (`msm`)

The following tools wrap the corresponding functions of the MariaDB Schema
Management (`msm`) plugin:

| MCP tool | Wraps (`msm_plugin/management.py`) |
| --- | --- |
| `msm.create_project` | `create_new_project_folder` |
| `msm.get_project_information` | `get_project_information` |
| `msm.set_development_version` | `set_development_version` |
| `msm.get_released_versions` | `get_released_versions` |
| `msm.get_last_released_version` | `get_last_released_version` |
| `msm.get_last_deployment_version` | `get_last_deployment_version` |
| `msm.prepare_release` | `prepare_release` |
| `msm.get_sql_content_from_section` | `get_sql_content_from_section` |
| `msm.set_section_sql_content` | `set_section_sql_content` |
| `msm.generate_deployment_script` | `generate_deployment_script` |
| `msm.get_deployment_script_versions` | `get_deployment_script_versions` |

Because the server is only started from a non-interactive shell, the wrapped `msm`
functions run in non-interactive mode and return their results directly instead of
prompting for input.

## Installation

The plugin is installed by copying the `mcp_plugin` folder into the MariaDB/MySQL
Shell plugins directory:

- Windows: `%appdata%\MySQL\mysqlsh\plugins`
- Others: `~/.mysqlsh/plugins`

The plugin is loaded automatically the next time the shell starts. It depends on the
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and on the
sibling `msm_plugin`.

### Installing the Python Requirements

When inside the repository root, run the following command in the terminal.

```bash
mariadb-shell --pym pip install -r mcp_plugin/requirements.txt
```

## Usage

The server is started from the command line and runs until terminated:

```bash
# streamable-HTTP transport (default)
mariadb-shell -- mcp start-server --port=8080

# stdio transport
mariadb-shell -- mcp start-server --transport=stdio
```

The `mcp.info()` and `mcp.version()` functions can be called from an interactive
shell:

```bash
mariadb-shell --py
> mcp.info()
> mcp.version()
```

Show the built-in help for the plugin with:

```text
\? mcp
```

## Running the tests

The tests are run with pytest inside the shell. From the `mcp_plugin` directory:

```bash
mariadb-shell --log-level=debug3 --verbose=4 --py -f run_tests.py
```

`run_tests.py` symlinks `mcp_plugin` (and the sibling `msm_plugin`) into a shell user
config home, then runs the suite via `mysqlsh --pym pytest`. Set `MYSQLSH` to select
the shell binary and `MYSQLSH_USER_CONFIG_HOME` to reuse a config home. The tests
launch the MCP server as a stdio subprocess and drive it with the MCP client SDK; the
stored connections, allowed paths and any created project folders are removed
afterwards.

## License

This plugin is released under the terms of the GNU General Public License,
version 2.0. See the [LICENSE](LICENSE) file for the full license text.

Copyright &copy; 2026, MariaDB plc and/or its affiliates.
