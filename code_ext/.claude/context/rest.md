# The MariaDB REST Daemon

A sandbox deployed with the MariaDB REST Service, a REST Daemon set up for
any connection, the REST Daemons view, and the Actions rows all of them
report. Built on `wip/restd_support` (2026-10-14). The daemon itself is
run by the MCP server's `rest` group: see the plugin's
`mcp_plugin/.claude/context/rest.md` for how mariadb-restd is driven.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## What the user decided (2026-10-14)

- **New Sandbox** gets a checkbox **Configure MariaDB REST Service** below
  the password choice, its note "Setting up the MariaDB REST Service on
  the Sandbox and start a MariaDB REST Daemon to serve the REST
  endpoints". Ticked, it shows REST Daemon Port next to REST Service Name
  (`/myService/v1`, its hint the URL `https://localhost:<port><path>`
  with what is typed), then REST User Name (`dba`) next to REST User
  Password, with the Configure dialog's password rules. Ticking it with
  no REST password takes the cursor there.
- **The REST Daemon port** of a sandbox is ten times the sandbox's (3311 ->
  33110) when that is a port and free, else the first free from 8443 up;
  the field's placeholder shows it, and it is left to the server unless
  typed. A connection's daemon: 8443 or the next free one.
- **After the deploy**, in this order: `rest.configure_restd` (which
  configures the REST metadata where there is none, installs mariadb-restd
  where it is missing, sets the daemon up and starts it), then the MRS auth
  app, the REST user, and the REST service, published and linked to `MRS`.
  The REST SQL is the MRS dialogs' own builders
  (`defaultAuthAppStatements`, `serviceDefaults` / `serviceStatements`).
- **A failure after the deploy keeps the sandbox**; the user sets the
  daemon up again from the connection's menu.
- **The Actions rows are sub-rows** of the sandbox's row, as a SQL
  script's statements are of its run: `ActionGroup` (`src/rest/
  actionGroup.ts`). The same for start / stop / delete of a sandbox that
  has a daemon: the daemon after the server on start, before it on stop
  and delete (`followSandbox`).
- **A failed row offers its log**: a `$(go-to-file)` button (the Tasks
  view's Show Log icon) beside the ↗ and → arrows, from `IActionRow.
  logPath`. Failed dump & load task rows have it too, for their task log.
  A setup's failure text (all mariadb-restd printed) is written to
  `<storage>/restd-logs/`; a daemon that did not start points at its own
  `log/mariadb-restd.log`.
- **The REST Daemons view** lists `rest.get_restd_status`: every
  deployment, the sandboxes' and `~/.mariadb-restd`'s, running or stopped,
  with Start / Stop / Delete / Open Log / Open in Browser. Delete drops the
  account the setup created, through the deployment's connection; where
  that server cannot be reached it offers to delete the folder only.
- **Set Up MariaDB REST Daemon…** on a connected connection's menu: the
  MRS dialog kind `restd` - port, the account's password (empty for the
  stored one; the server tests it and the privileges), and the metadata
  schema where the server has several. A refusal stays in the dialog.

## Files

- `src/mcp/restApi.ts`: the `rest.*` tools; setup's timeout is 20 minutes
  (an install), the others 2.
- `src/rest/actionGroup.ts`: one action with a row per step.
- `src/rest/sandboxRest.ts`: `deploySandboxWithRest`, `followSandbox`.
- `src/rest/restdCommands.ts`: the view's and the connection menu's
  commands, `writeRestdLog`.
- `src/tree/restdTreeProvider.ts`: the view.
- `src/sandboxes/sandboxFields.ts`: the REST fields, `defaultRestdPort`,
  `restServiceUrl`, `sandboxRestOptions`.
- `src/mrs/mrsDialogs.ts`: the `restd` dialog's fields and validation;
  `servicePathProblem` and `defaultAuthAppStatements`, shared with the
  sandbox dialog.
- `ui-test/tests/07-restd.test.ts`, suite `restd` (`npm run
  ui-test:restd`): needs a mariadb-restd where the server looks for one.

## Gotchas

- **The extension calls `rest.start_restd` / `stop_restd` /
  `delete_restd_config` although the MCP sandbox tools do the same**: the
  server's hooks are what keeps an agent's sandbox right; the extension's
  calls (which do nothing where it is done already) are what gives each
  step its row, with its error.
- **The panel stays on General Actions while a sandbox's steps run**
  (2026-10-14, the user's call): the REST steps open a connection to the
  new sandbox, and the first connection to report anything takes over a
  panel that is on General Actions by default. `ResultViewProvider.
  holdGeneralActions()` blocks that until the last step ends; the deploy
  with the REST Service and `followSandbox` hold it. A connection the user
  picked is never touched.
- **A grouped deploy is `deploy(options, quiet=true)`**: the logging
  sandbox API leaves its own row out then, or the deploy would show twice.
- **The connection's REST Daemon lives in the real `~/.mariadb-restd`**
  during the UI test too; the test deletes it again.
