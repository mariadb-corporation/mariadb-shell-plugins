# MariaDB MCP Server Plugin

This folder contains the code for the **MariaDB MCP Server Plugin**. It hosts the
Model Context Protocol (MCP) server functionality for the MariaDB AI Plugins,
exposing their capabilities to MCP-compatible clients such as AI assistants and
agent frameworks.

The plugin registers the global `mcp` object in the MariaDB Shell and runs an
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) server in the
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
| `mcp.setup()` | Interactively configures the allowed connections and paths, and installs the migration tooling. |
| `mcp.start_server(options)` | Starts the MCP server; blocks until terminated (`host`, `port` options). |

## Configuration (`mcp.setup()`)

Before starting the server, run `mcp.setup()` from an interactive shell to configure
what the MCP server is allowed to access, or run the following command on the terminal:

```bash
mariadb-shell -- mcp setup
```

Everything below can also be given as command-line options instead, for use where there
is no terminal — see [Non-interactive setup](#non-interactive-setup).

- **Connections**: enter a MariaDB connection URI (e.g. `user@host:3306`). The
  password is prompted for and the connection is verified with `shell.open_session()`
  before the password is stored in the shell secret store under the key
  `MCP:Connection:<uri>`, with the URI normalized first (see below), so that one
  connection is configured under one spelling. The `db.*` tools only allow the
  connections configured here.
- **Allowed paths**: choose the local directories the server may access (the current
  directory is suggested as the default, shown as a full path). These are stored in a
  `settings.json` file in the plugin data directory.
- **Migration tooling** (menu only, Linux and macOS only): downloads the
  [MySQL-to-MariaDB migration tooling](https://github.com/mariadb-corporation/Mysql-to-MariaDB-Migration)
  and extracts it into `~/.local/share/mariadb-migrator/<version>`.
  See [Migration tooling](#migration-tooling) below.

> Note: The MariaDB connections configured during the setup procedure are stored
> separately from the regular MariaDB Shell connections. Otherwise, the LLM would
> have full access to all of the user's stored connections, which would pose a
> security risk. To retrieve the password stored for an MCP connection, call the
> `shell.list_secrets()` function to list all secrets. Then, look for the
> `MCP:Connection:` prefix and call `shell.read_secret()` for the given entry.

On the first run, `mcp.setup` walks through adding connections and then paths. On
subsequent runs it presents a menu to add or delete connections and paths and to manage
the migration tooling. The tooling is not part of the first-run walkthrough - it is a
download nothing else here depends on, so it is only ever installed by asking for it
from the menu.

That menu entry offers whichever of the two steps applies: **Download** when nothing is
installed, and **Remove** when something is - there is no update step, so installing a
different release means removing the installed one and downloading again. Removal is the
one step the setup does not suggest going ahead with, and it takes any leftovers of an
interrupted download with it.

On **Windows** the entry is not shown at all, and the menu numbers its remaining choices
accordingly - the tooling is a POSIX shell entry point driving a directory of shell
scripts, so there would be nothing to run what a download installed.

**No system Python is required.** The MariaDB Shell bundles a complete CPython, and the
download uses it to build the tooling's virtual environment - so the tooling runs on a
machine that has no `python3` of its own at all.

### Non-interactive setup

Every menu item is also a command-line option, so a provisioning script or a CI job can
configure the server without a terminal. With **no** options `mcp setup` is the
walkthrough above; with **any** option it does exactly what the options say and asks
nothing else. Run `mariadb-shell -- mcp setup --help` for the generated list.

| Option | What it does |
| ------ | ------------ |
| `--addConnection=<uri>` | Verify and store one connection. |
| `--password=<str>` | Its password. **Discouraged** — a command line is visible in `ps` and lands in shell history. |
| `--passwordEnv=<VARNAME>` | Read it from the *named* environment variable, so the password itself is never in the command line. |
| `--passwordStdin` | Read it from the first line of stdin, for piping from a secret manager. |
| `--noVerify` | Store without opening a session first, for a server that is not up yet. |
| `--deleteConnections=<list>` | Delete connections, by URI in any spelling that names them. |
| `--addPaths=<list>` | Allow directories (each must already exist). |
| `--deletePaths=<list>` | Stop allowing directories. |
| `--installMigrator` | Download, provision and wrap the migration tooling. |
| `--removeMigrator` | Remove every installed release, and the wrapper. |
| `--nonInteractive` | Never prompt; a missing password is an error, not a question. |
| `--show` | Print the configuration and do nothing else. |
| `--json` | Print what `--show` reports as JSON. Only applies to `--show`. |

Lists are comma-separated. Repeating an option is **not** a supported way to build one.
`--addConnection` is the exception to the list rule: it takes one connection, because
each needs its own password.

```bash
# CI: from a secret the runner injected
mariadb-shell -- mcp setup --addConnection=mig@source-db:3306 --passwordEnv=SRC_PW --nonInteractive

# From a secret manager
vault read -field=pw db/src | mariadb-shell -- mcp setup --addConnection=mig@source-db:3306 --passwordStdin

# Paths and the tooling in one call
mariadb-shell -- mcp setup --addPaths=/srv/projects,/tmp/work --installMigrator

# Reinstall the tooling: removal runs first, so this is the idiom rather than a conflict
mariadb-shell -- mcp setup --removeMigrator --installMigrator

# Machine-readable state
mariadb-shell -- mcp setup --show --json
```

At most **one** password source may be given — which one was meant is not guessed at —
and a URI carrying a password (`user:pw@host`) is refused rather than used, since
normalization strips it and the connection would end up stored with no password at all.

Deletions are carried out **before** additions, and the migration tooling last, so
deleting and re-adding the same connection in one call ends up with it added. Anything
that fails stops the run, leaving what already succeeded in place and reported, so a
script can tell how far it got. Storing a connection that is already configured updates
its password, so a provisioning script is safe to run twice.

### Migration tooling

The release configured as `MIGRATOR_VERSION` in `lib/general.py` (currently
`v1.4.0-beta`) is downloaded from GitHub as a source archive and extracted into

```
~/.local/share/mariadb-migrator/<version>/
```

with the archive's own top-level directory stripped, so the tooling's entry point sits
at `~/.local/share/mariadb-migrator/v1.4.0-beta/mariadb-migrator`. The recorded file
modes are restored, so the entry point and the `scripts/*.sh` remain executable.

The base directory is `$XDG_DATA_HOME` when that is set to an absolute path, and
`~/.local/share` otherwise. This is deliberately **not** the plugin data directory: the
tooling is a standalone program that outlives any one plugin install, so it is installed
where such a program belongs rather than somewhere only this plugin would look for it.

**The release is part of the path**, which makes the directory name the one authoritative
record of what a copy is - there is no version file inside an install that could disagree
with it - and means installing a different release never extracts over the top of an
existing one. To install a newer release, change `MIGRATOR_VERSION` to its tag, remove
what is installed, and download again.

Downloading is atomic in effect: the new copy is extracted into a dot-prefixed working
directory beside the release directories and only moved into place once it is complete,
so a download that fails part-way through leaves an installed copy exactly as it was.

### What a download installs

Downloading does three things, each reported separately so a partial install says which
step stopped:

1. **Extracts** the release into `~/.local/share/mariadb-migrator/<version>/`.
2. **Builds the virtual environment** at `<version>/.venv` and installs
   `orchestrator/requirements.txt` into it. The environment is created with the
   interpreter the shell bundles, so no system `python3` is involved; the result is an
   ordinary venv whose `bin/python3` is a real interpreter, exactly as
   `python3 -m venv` would produce. `pip` runs with `--require-virtualenv`, so a wrong
   path can never install into the shell's own site-packages. `.venv` is the name the
   tooling's own launcher looks for.
3. **Installs a wrapper** at `~/.local/bin/mariadb-migrator`, so the tooling is runnable
   by name:

   ```bash
   mariadb-migrator --help
   ```

   The wrapper `cd`s into the install directory and activates the virtual environment
   before handing over. Both are necessary: the tooling resolves `scripts/`,
   `orchestrator/` and the default `config/` paths relative to the working directory,
   and its own dependency check runs *before* it would activate `.venv` itself. Because
   it runs from the install directory, **relative arguments are relative to there** -
   `--out artifacts/plan` writes inside `<version>/artifacts/plan`.

   If `~/.local/bin` is not on your PATH the setup says so and prints the `export` line
   to add. A wrapper from an earlier release is overwritten; a file of that name that
   mcp.setup did **not** create is never touched - the setup reports it and leaves it
   alone.

**Removal takes every installed release**, not just the configured one, clearing
`~/.local/share/mariadb-migrator` outright, and takes the wrapper with it (a wrapper
pointing at a tree that is gone is worse than none). Releases accumulate there as the pin moves
on, and a release the pin has moved past would otherwise have no way of being removed
again short of deleting it by hand. The menu therefore offers **Remove** whenever
anything is installed - including a release older than the configured one - and
**Download** only when the directory is empty or absent.

The tooling runs on **Linux and macOS** only. `mcp.setup` therefore leaves the step out
of the menu on Windows rather than installing something that cannot be run there; the
plugin's own features do not depend on it. The code lives in `lib/setup_migrator.py`,
separate from the rest of the interactive setup in `lib/setup.py` (both share the prompt
primitives in `lib/setup_prompts.py`).

## Exposed MCP tools

The tools are grouped into function groups that can be loaded independently via the
`function_groups` option of `mcp.start_server` (`db`, `msm`, `sandbox`, `migrator`;
defaults to all):

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
| `db.connect` | Opens a configured connection (`shell.open_session()`) and returns a connection UUID. The URI need not be spelled exactly as listed (see [Which URI names which connection](#which-uri-names-which-connection)). |
| `db.list_schemas` | Lists the schemas available on a connection UUID, with their type (system or user) and comment. |
| `db.list_objects` | Lists the objects of one type (`table`, `view`, `function`, `procedure`, `sequence`, `trigger`, `event`; defaults to `table`) in a schema. |
| `db.get_object_details` | Describes one object in detail: columns, constraints, foreign keys in both directions, parameters, or type-specific properties. |
| `db.execute_sql` | Runs a single SQL statement (with optional parameters) on a connection UUID. |
| `db.execute_sql_script` | Runs a multi-statement SQL script on a connection UUID. |
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
| `msm.deploy_schema` | `deploy_schema` |

Because the server is only started from a non-interactive shell, the wrapped `msm`
functions run in non-interactive mode and return their results directly instead of
prompting for input.

`msm.deploy_schema` deploys onto a connection opened with `db.connect`, so it is only
registered when the `db` function group is served as well. All other `msm` tools work
on a schema project on disk and are always available.

### Sandbox tools (`sandbox`)

Tools for deploying and managing local MariaDB/MySQL sandbox instances, wrapping the
shell's `sandbox` global object. Sandbox instances are only meant for local testing.

> Note: A `mariadbd` server binary needs to be in the PATH. Install the
> MariaDB Server on your developer machine before using the sandbox tools.

| MCP tool | Wraps |
| --- | --- |
| `sandbox.deploy` | `sandbox.deploy` |
| `sandbox.start` | `sandbox.start` |
| `sandbox.stop` | `sandbox.stop` |
| `sandbox.kill` | `sandbox.kill` |
| `sandbox.delete` | `sandbox.delete` |
| `sandbox.vendor` | `sandbox.vendor` |
| `sandbox.version` | `sandbox.version` |

### Migrator tools (`migrator`)

| MCP tool | Purpose |
| -------- | ------- |
| `migrator.set_config` | Writes the tooling's `config/migration.yaml`. |
| `migrator.plan` | Generates a migration plan; executes nothing. |
| `migrator.run` | Executes the migration steps. |
| `migrator.resume` | Resumes a failed run from its `state.json`. |

These are registered **only where the migration tooling is installed** (see
[Migration tooling](#migration-tooling)). On a server that never ran the download step
the group registers nothing rather than advertising tools whose every call would fail,
so installing the tooling takes effect on the next server start.

Three things are deliberately not in the client's hands:

- **Which servers can be reached at all.** A configuration may only name accounts that
  are among the connections `mcp.setup` configured. `migrator.set_config` composes the
  connection URI from the configuration's own fields - `SRC_ADMIN_USER` at
  `SRC_HOST`:`SRC_PORT`, `TGT_ADMIN_USER` at `TGT_HOST`:`TGT_PORT`, `REPL_USER` on the
  source - and **refuses to write the file at all** if any of them is not a configured
  connection. A client therefore cannot point a migration at a server it was never
  given, not even to have the attempt fail later. A side that names a host must also
  name the account to reach it by, since otherwise there would be nothing to check
  against. The check is applied to the *merged* result, so two individually harmless
  `merge` calls cannot add up to a forbidden connection; and it runs **again before
  every migration**, so removing a connection with `mcp.setup` stops the runs already
  configured against it.
- **Passwords.** `migrator.set_config` refuses `SRC_PASS`, `SRC_ADMIN_PASS`, `TGT_PASS`,
  `TGT_ADMIN_PASS` and `REPL_PASS`, so no password is ever written to disk by this
  plugin. Each is read from the shell's secret store when a migration runs, under the
  matching configured connection. The result reports which connection answered, never
  the secret.
- **The working directory.** Every run happens in the install directory, so `out` must be
  relative to it; an absolute path is refused.

Each run returns its exit code, the tail of the orchestrator's output, the artifacts
directory and that run's `report.json` when it wrote one. Every invocation is given a
closed stdin: the orchestrator prompts for anything missing from the configuration - and
`plan` has no `--non-interactive` flag at all - so without that an incomplete
configuration would hang a tool call instead of reporting what was absent.

A migration can outlast any client's patience. `timeout` defaults to an hour, and
because the artifacts directory comes back either way, a run that outlives its timeout
can still be followed from its own files and picked up with `migrator.resume`.

## Installation

The MCP server plugin ships with the MariaDB Shell 26.9.0 and later. No manual
installation is required.

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

## Database connection behavior

To understand the MCP server database connection behavior, please read the
sections below.

### The MCP server has no authentication

This MCP server implementation is designed for agent-based development on a
local developer's machine.

**Anyone who can reach the MCP server's port can use the stored database
credentials.** There is no authentication of any kind: no token, no password,
no client certificate. A client that can connect may call `db.list_connections`
to see which connections are configured and `db.connect` to open one, and the
server then opens it with the password kept in the shell's secret store. Whoever
reaches the port has, in effect, the access of every connection configured with
`mcp.setup`.

The only thing standing in for access control is **where the server listens**. It
binds to `127.0.0.1` by default, so only clients on this machine can reach it.
Starting it with a non-loopback `--host` prints a warning to stderr and is a
decision to hand that database access to the network; if the server has to be
reachable remotely, put it behind a tunnel or an authenticating reverse proxy
rather than exposing it directly.

Note also that the sandbox tools can start database servers and the `msm` tools
can read and write files within the allowed paths, so the same reachability
applies to those.

### Requests from a browser are refused

Because there is no authentication, a page open in a browser that can reach the
port would otherwise be able to drive the database tools. Normally the browser's
cross-origin rules stop it from reading the answers, but a DNS-rebinding attack
removes even that: the attacker resolves their own name to the address the server
listens on, which makes their page same-origin with it.

The server therefore validates the `Host` header of every request against the
names it actually answers to, and refuses anything else with `421`. A page loaded
from `evil.example.com` sends that name, which is not one of them. An `Origin`
the server does not serve is refused with `403`, while requests carrying no
`Origin` at all (which is every non-browser client) are unaffected.

The accepted names are derived from `--host`:

| `--host` | Accepted `Host` values |
| --- | --- |
| loopback (the default) | `127.0.0.1`, `localhost`, `[::1]`, each also with the port |
| a single address or name | that host, with and without the port |
| `0.0.0.0` / `::` | loopback, plus this machine's hostname, FQDN and resolved addresses |

If the server is reached under a name none of those cover - through a reverse
proxy, a port forward or a DNS alias - name it with `--allowed-hosts`, otherwise
those requests are refused:

```sh
mariadb-shell -- mcp start-server --host=0.0.0.0 --allowed-hosts=mcp.example.com
```

This is configured explicitly rather than left to the MCP SDK, which turns the
protection on only when the host is written exactly `127.0.0.1`, `localhost` or
`::1` - so `LOCALHOST`, `127.0.0.2` or any non-loopback bind would otherwise be
served with no `Host` or `Origin` validation at all.

### Connection handling over HTTP

Served over stdio, the server talks to a single client - the process that started
it - for its entire lifetime. Served over HTTP it is reachable by any client that
can reach the port, which is what the following two safeguards are there for.
They stop one client from taking over another's connection; they are not a
substitute for the authentication described above.

- **A connection belongs to the client that opened it.** It is bound to two
  things: the MCP session `db.connect` was called on, and the IP address it was
  called from. A request that does not match both is answered exactly as one
  naming a connection UUID that was never handed out, so a connection cannot be
  taken over by guessing its UUID.
  - The **MCP session id** (`Mcp-Session-Id`) is the part that does the real work.
    The server generates it when a client initializes, so it is a secret only that
    client has been told, and it keeps clients apart even when their addresses are
    identical - as they are behind one NAT or reverse proxy, and as they are for
    every process on the machine when the server is bound to loopback.
  - The **IP address** comes from the peer address of the connection the request
    arrived on, never from a header (which a client can forge). To keep that
    trustworthy the server is run with uvicorn's proxy-header handling disabled -
    left at its default, uvicorn would replace the peer address with the
    `X-Forwarded-For` header of any request from a trusted address, and loopback
    is trusted by default. Consequently, running behind a reverse proxy collapses
    every client onto the proxy's address, which is exactly why the binding does
    not rest on the address alone. Addresses are compared in a normalized form, so
    a client reaching the server over IPv4 on one call and IPv6 on the next is
    still the same client.

  Over stdio a request has neither a peer address nor a session id, so the
  connection is bound to "no client" and the single client keeps matching it; the
  comparison itself is always made, and never conditional on the transport.
- **An unused connection is closed after 30 minutes.** A background reaper closes
  the database session of every connection that has been unused for that long,
  releasing the connection on the server. The connection UUID stays valid: the
  next tool call using it opens a new session transparently, while `db.close`
  simply drops it without opening anything. As it is a new session, nothing that
  only lived in the previous one - temporary tables, session variables, the
  current schema, an open transaction - survives an idle period. The call that
  opens the new session says so: its result carries **`session_restarted: true`**
  (on the first entry, for `db.execute_sql_script`). That matters most for the
  case a client cannot otherwise detect - a `COMMIT` on a session that never saw
  the `START TRANSACTION` succeeds and commits nothing. The flag describes that
  one call, and is absent from every other result.
- **No connection lives longer than 12 hours.** This is a different limit from the
  one above and applies to the connection rather than to its session: twelve hours
  after `db.connect` returned it, the UUID stops working however much it has been
  used in between, and the client has to call `db.connect` again. Expiring is
  reported exactly like a UUID that was never handed out, and it is applied both
  when a connection is used - so it holds over stdio as well - and by the reaper,
  so a connection whose client simply went away does not sit in the server for
  the rest of its life.
- **At most 16 connections per client, and 64 in total.** Opening one costs the
  client a single tool call and the server a real database session, so a loop of
  `db.connect` calls would otherwise be able to use up the database's
  `max_connections` and grow this process's memory unchecked. A call over either
  limit is refused with an error naming `db.close`, before the database is asked
  for anything - the refusal costs no connection. Both limits are well above what
  a client needs in practice; over stdio, where every request looks like the same
  client, the per-client limit is the one that applies.

### Which URI names which connection

`db.list_connections` hands out the configured URIs as `user@host:port`, but a
client that composes a URI itself tends to write a scheme in front of it or leave
out the default port. `db.connect` therefore resolves the URI it is given to the
one the connection is configured under, rather than comparing the two as strings:
a `mariadb://` or `mysql://` prefix, a missing port, the case of the host and a password written
into the URI make no difference.

What the URI says beyond that does, and has to match: a URI naming a default
schema or a connection option the configured connection does not name is refused
rather than answered with the configured connection, which would quietly not do
what it asked for - `?ssl-mode=REQUIRED` on a session opened without TLS being
the case that matters.

What is opened, logged and re-checked against the configuration is always the
configured URI.

### Removing a connection revokes it

Deleting a connection with `mcp.setup` - or deleting the sandbox that registered
one, with `sandbox.delete` - also invalidates the UUIDs that are open on it. The
URI is checked against the configured connections **every time a session is
opened**, not only by `db.connect`: since the stored password is read again on
each open, a session reopened after an idle period would otherwise come back on a
connection that had been taken away, and a UUID that never expired would go on
working for as long as the server ran.

The two limits are what bound the rest of it: a connection whose session is still
open is not re-checked on every statement (that would mean a secret-store lookup
per SQL statement), so a connection in continuous use can outlive its removal by
up to its 12-hour lifetime. Restart the server if a removal has to take effect at
once.

### A connection whose session dies recovers by itself

A database session can be taken away without anyone closing it: the server is
restarted, an administrator `KILL`s the connection, or a firewall or load
balancer between the two drops it for being idle sooner than the 30 minutes
above. The shell cannot tell such a connection from a live one - it only knows
whether it still holds a handle locally - so the failure surfaces as the statement
that hits it: `MySQL Error (2013): Lost connection to server during query`.

That statement fails and the error is reported as it is; it is not retried, since
it may have run in part and anything it had open (a transaction above all) went
with the connection. But the dead session is thrown away, so the **next** call on
that connection UUID opens a new session and works. The connection does not have
to be closed and reopened, and a client that simply retries its statement will
succeed.

### What the server logs

Because a refused request is answered exactly like one naming a connection that
does not exist, nothing about an attempted takeover reaches the client - so the
server writes what happens to its connections to **stderr**, one line per event,
whichever transport is in use:

```text
2026-08-07T14:03:11+0200 [mcp] db.connect: opened connection 6f2a91c4... on 'root@127.0.0.1:3306' for address=192.0.2.10 session=0123abcd...
2026-08-07T14:07:44+0200 [mcp] db: REFUSED use of connection 6f2a91c4... bound to address=192.0.2.10 session=0123abcd... by a request from address=192.0.2.20 session=fedc4321...
2026-08-07T14:37:44+0200 [mcp] db: closed the idle session of connection 6f2a91c4... (address=192.0.2.10 session=0123abcd...) after 1800s unused; the connection stays valid and opens a new session when it is used again
```

Recorded are: a connection opened (with the client it is bound to and the URI it
was opened on), a use refused, a `db.connect` refused because the client could
not be fully identified, a session closed for being idle, a session that failed
to close, and a failing pass of the idle reaper. Redirect stderr to a file to
keep the trail:

```sh
mariadb-shell -- mcp start-server --port=8080 2>> ~/mcp-server.log
```

Connection UUIDs and MCP session ids appear **truncated to their first eight
characters**: both are credentials - holding one is what lets a client use a
connection - so the log is not a place they can be read out of. Nothing else
about a request is logged; the SQL statements a client runs are not.

## Running the tests

The tests are run with pytest inside the shell. From the `mcp_plugin` directory:

```bash
mariadb-shell --log-level=debug3 --verbose=4 --py -f run_tests.py
```

`run_tests.py` symlinks `mcp_plugin` (and the sibling `msm_plugin`) into a shell user
config home, then runs the suite via `mariadb-shell --pym pytest`. Set `MARIADB_SHELL` to
select the shell binary and `MARIADB_SHELL_USER_CONFIG_HOME` to reuse a config home
(the pre-rename `MARIADB_SHELL_USER_CONFIG_HOME` is still honoured). The tests
launch the MCP server as a stdio subprocess and drive it with the MCP client SDK; the
stored connections, allowed paths and any created project folders are removed
afterwards.

### End-to-end tests

Tests marked `e2e` are **not part of a standard run** and are reported as skipped.
Each one deploys its own source and target servers, reaches the network and installs
the migration tooling, which is more than a routine test run should do. Add `--e2e`
to run them as well:

```bash
mariadb-shell --py -f run_tests.py --e2e
```

There is one at present, `tests/unit/test_migration_e2e.py`: it deploys a MySQL
source and a MariaDB target with `sandbox.deploy`, registers both through the
`mcp setup` command line, creates a schema on the source with the `db.*` tools,
installs the migration tooling with `mcp setup --installMigrator`, migrates with
`migrator.set_config` / `migrator.run`, and then checks every migrated object and
row on the target. It skips itself when the machine cannot run a migration - no
MySQL server to deploy the source from being the usual reason; see the module
docstring for the rest.

## License

This plugin is released under the terms of the GNU General Public License,
version 2.0. See the [LICENSE](LICENSE) file for the full license text.

Copyright &copy; 2026, MariaDB plc.
