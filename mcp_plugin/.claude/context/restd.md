# The restd group: MariaDB REST Daemon deployments

`restd.configure_restd`, `restd.start_restd`, `restd.stop_restd`,
`restd.delete_restd_config`, `restd.get_restd_status`, and the hooks that make a
sandbox's daemon follow the sandbox. Built on `wip/restd_support` (2026-10-14)
for the VS Code extension's "Configure MariaDB REST Service" sandbox option.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Architecture / key decisions

The user's decisions (2026-10-14), and why the code looks the way it does:

- **Where a deployment lives.** A sandbox's: `<sandboxDir>/<port>/mariadb-restd`.
  Any other server's: `~/.mariadb-restd/<host>_<port>` (Windows
  `%APPDATA%\MariaDB\mariadb-restd`). A metadata schema other than
  `mariadb_rest_service` adds `_<schema>` (`mariadb-restd_<schema>`,
  `<host>_<port>_<schema>`): a daemon serves a server's metadata schema,
  whatever account a connection uses, so the folder is not per connection
  (connections have no stable ID either; the user agreed, 2026-10-14).
  A server reached through a socket is named by itself, not by the socket
  (`restd.server_deployment`, 2026-10-15): `localhost_<@@port>`, so a TCP
  connection to it finds the same deployment; only a server listening on
  no port gets `socket_<file name>_<8 hex of sha256(path)>`. `deployment.json`
  records the socket either way, which the extension matches a socket
  connection on.
  The extension finds a connection's daemon by `deployment.json`'s host,
  server port and metadata schema, not by the folder name. `deployment.json` beside
  `mariadb-restd.conf` records the connection URI, host, server port and
  `sandbox_port`; the rest comes from the configuration.
- **Ports.** Every daemon, a sandbox's included: the first free port from
  8443 up (the user's call, 2026-10-14; a sandbox's used to get ten times
  the sandbox's port). A stopped deployment's port counts as taken
  (`restd.taken_ports`).
- **Metadata.** One schema: used. Several and none named: refused, listing
  them. None: `CONFIGURE REST METADATA ENABLED` (with `SCHEMA` when named).
- **Password.** The given one, else the connection's stored one
  (`db_functions.connection_credentials`). It is tested by opening a session of
  its own with it, then the privileges `--setup` needs are checked from
  `SHOW GRANTS` (`restd_functions._missing_privileges`).
- **Install.** `restd.find_restd`: PATH, `~/.local/bin`, then the newest
  version folder under `~/.local/share/mariadb-restd` (Windows
  `%LOCALAPPDATA%\Programs\mariadb-restd`), the shell's install layout. If
  none: `restd_install.install` runs the rest-daemon repo's `install.sh` /
  `install.ps1` (`MARIADB_RESTD_TAG`, `_PREFIX`, `_BINDIR`, as the shell's).
  `--gui`: without asking. Agent mode: MCP elicitation first.
- **Sandbox hooks live in the `sandbox.*` tools**, not only in the extension,
  so an agent deleting a sandbox never leaves a daemon serving from a deleted
  folder. They work whether or not `restd` is served. `sandbox.start` starts
  the daemon after the server; stop/kill stop it first; delete stops it (only
  if the server is not running, else the delete is refused and nothing is
  touched) and the sandbox's rmtree removes the folder. A daemon that fails
  never fails the sandbox's call. The four tools answer `{message, restd}`:
  `restd` has one entry per daemon (`restd.status` of it, plus `error`, None
  where it worked), which the extension makes its Actions rows of, so it
  makes no daemon calls of its own (2026-10-14).
- **Delete drops the generated account** (`mariadb_restd_<random>`, matched by
  pattern, so an account the user named is never dropped) and the
  `rest_daemon` row, through a `connection_id`; `drop_accounts=False` deletes
  the folder only. A failed row delete (FKs from `rest_daemon_status`) is
  reported, not fatal.

- **Paths.** start/stop/delete take a deployment `directory`. One the
  server lists (`restd.deployment_dirs`: the default sandbox path's and
  the deployments root's) needs no allowed path - configure_restd made it
  without asking, so an agent would otherwise be asked to trust a folder
  the user never chose. Any other folder goes through
  `require_allowed_path` (found by the mariadb-shell docs-ref review,
  2026-10-14).

## How mariadb-restd is driven (verified against the source and a real 26.10.0)

- **`--setup` has no password option**: the admin password goes to its stdin.
  It prompts on stdout and reads a line, with or without a TTY.
- **Always pass `--jwt-secret=<hex>`**: without it the setup prompts and loops
  until it reads a non-empty line - forever at EOF.
- **`start.sh` is not used.** It runs the daemon with a bare `&` (no detach,
  inherits stdout, exit code always 0). `restd.start` spawns
  `mariadb-restd -c <conf>` itself, `MARIADB_RESTD_PID=<dir>/run/mariadb-restd.pid`
  (and `ROUTER_PID`, the name a daemon older than 26.10.0 reads),
  `start_new_session` (POSIX) / `CREATE_NO_WINDOW` (Windows) like the shell's
  sandbox, console output to `log/console.log`, then waits until the port
  accepts connections or the process exits.
- **A stale pid file blocks a start** ("PID file ... found. Already
  running?"), so `start` removes one whose process is gone. `stop` sends
  SIGTERM, waits 30 s, then kills, and removes the pid file.
- `--setup` needs the REST metadata to exist and MariaDB 11.7+. It creates no
  REST service and no REST user. On failure it rolls back its files and
  accounts but NOT the `rest_daemon` row (an upsert keyed on name+address).

## Files that matter

- `lib/restd.py`: folders, finding the executable, ports, `setup`, `start`,
  `stop`, `status`, `delete_directory`, `deployment_dirs`.
- `lib/restd_install.py`: the install script download and run.
- `lib/restd_functions.py`: the tools; `configure_restd` registers only with
  `db`. `_parse_uri` / `_open_session` are the test seams.
- `lib/sandbox_functions.py`: `_restd_dirs` (one per metadata schema, from
  `restd.sandbox_deployment_dirs`) and the hooks, which follow every one.
- `tests/unit/test_restd.py`: a fake `mariadb-restd` (a Python script behind a
  `/bin/sh` wrapper, like test_util's fake shell) that writes a config and
  listens on its port; and `test_a_sandbox_really_gets_a_rest_daemon`
  (`--e2e`), which uses the real binary.

## The HTTPS certificate (2026-10-14)

- **One certificate for every daemon** (the user's design): `lib/restd_certs.py`
  makes a local CA and a `localhost` server certificate, which the first
  `configure_restd` creates in `<deployments root>/web-certs`.
  - The server certificate names `localhost`, `127.0.0.1` and `::1`.
  - Both are valid for 824 days, under macOS's 825-day limit, and are made
    anew 30 days before they end.
  - The CA's private key is not kept.
  - `restd.setup(..., certificate=)` passes `http_server.ssl_cert` and
    `ssl_key`, so the setup makes no certificate of its own.
- **Why not restd's own certificate:** its CA's common name is
  `MySQL_Router_Auto_Generated_CA_Certificate` in every deployment. Its
  server certificate has no subject alternative names, which browsers
  require for `localhost`.
- **Registering it:** `restd.trust_certificate` does what the MySQL Shell
  for VS Code's welcome wizard did.
  - macOS: `security add-trusted-cert -r trustRoot -k <login keychain>`,
    with the system's authorization dialog.
  - Windows: `certutil.exe -addstore -user -f ROOT`, with the Security
    Warning dialog.
  - Linux: NSS `certutil` into `~/.pki/nssdb`, without a dialog.
  - Unlike the wizard, it checks the exit code and then whether the CA is
    trusted, so a cancelled dialog is an error. The wizard took one for
    success. A dialog may wait 600 s.
  - An agent is asked first, through elicitation; in `--gui` mode the user's
    ticked box is the consent.
- **`restd.get_certificate_status`** makes nothing. The dialogs ask it so
  that they offer the box only while the CA is not trusted.
- **Trust is checked with the system itself:**
  - macOS: `security verify-cert -c server.crt -p ssl -s localhost`. A
    cancelled authorization leaves the certificate in the keychain,
    untrusted.
  - Windows: the SHA-1 fingerprint from `certutil -verifystore -user ROOT`.
  - Linux: the SHA-1 fingerprint from NSS `certutil -L`.
- **Tested:** the real-daemon e2e test checks that the daemon serves exactly
  `web-certs/server.crt`. The OS dialogs were not exercised by a test.

## Gotchas

- **A JWT secret from `token_urlsafe` can start with `-`**, which restd's
  option parser takes for the next option ("option '--jwt-secret' expects a
  value, got nothing"). Only the real binary showed it; the secret is now hex
  and attached with `=`.
- **`helpers.tool_payload` turns a one-element list into the element.** A test
  reading `restd.get_restd_status` has to wrap a dict back into a list.
- **A daemon this server started is its child**: once it exits it is a zombie
  that still answers `kill(pid, 0)`. `restd._is_alive` reaps it with
  `waitpid(WNOHANG)` first.

## Current state

- Built and tested on macOS: 29 unit tests plus the e2e test pass; the full
  suite 793 passed, 4 skipped on 2026-10-14.
- Not run on Windows or Linux yet. The Windows branches (`_is_alive` through
  `OpenProcess`, the `.exe` lookup, `install.ps1`) are untested.
- The rest-daemon repository is private, has no releases and no install script
  yet (2026-10-14), so the automatic install cannot work end to end; the user
  installed 26.10.0 by hand into the expected layout.

## Next steps

- The extension side (plan steps 2-5, see code_ext's context).
- mariadb-shell docs-ref: the tool group lists (separate PR).
