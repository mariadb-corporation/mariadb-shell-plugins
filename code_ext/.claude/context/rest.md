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
- **The REST Daemon port**, a sandbox's as a connection's: 8443, or the
  next free one (the user's call, 2026-10-14; a sandbox's used to get ten
  times its port). The field's placeholder shows 8443, and it is left to
  the server unless typed.
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
- `src/tree/restdTreeProvider.ts`: the view. `deployments()` hands out
  the list it last showed (fetched if it has shown none; `refresh()`
  clears it), which `src/rest/restdUrl.ts` matches tree rows against for
  their request URLs (see mrs.md).
- `src/sandboxes/sandboxFields.ts`: the REST fields, `DEFAULT_RESTD_PORT`,
  `restServiceUrl`, `sandboxRestOptions`.
- `src/mrs/mrsDialogs.ts`: the `restd` dialog's fields and validation;
  `servicePathProblem` and `defaultAuthAppStatements`, shared with the
  sandbox dialog.
- `ui-test/tests/07-restd.test.ts`, suite `restd` (`npm run
  ui-test:restd`): needs a mariadb-restd where the server looks for one.

## The HTTPS certificate (2026-10-14)

- **The box:** New Sandbox's REST section and the Set Up MariaDB REST Daemon
  dialog offer "Register the REST Daemon's HTTPS certificate with the
  system", ticked by default.
  - It is offered only while the system does not trust it yet: the panel
    asks `rest.get_certificate_status` as it loads (`restCertificateTrusted`
    in the sandbox dialog's load message, `certificateTrusted` in the restd
    dialog's context).
  - A status that cannot be read offers the box. That does no harm:
    registering a trusted certificate does nothing.
- **Its step:** "Registering the HTTPS Certificate" is a sub-row of its own,
  and in the sandbox flow the last one, so a cancelled system dialog fails
  only that row.
- **The UI tests untick it** (`declineCertificate`). Ticked, it would bring
  up a system dialog no test can answer.

## Gotchas

- **The extension makes no daemon calls of its own for a sandbox's start,
  stop or delete**: the MCP sandbox tools follow every daemon of the sandbox
  and answer `{message, restd}`, and `followSandbox` makes a row of each
  `restd` entry (with its log where it failed). Whether to put the action's
  group up front is told by the REST Daemons view's cached list; a daemon
  it does not know of yet gets its rows once the call says so.
- **A sandbox's steps can be watched on any connection**: they are General
  Actions, which every connection's Actions tab shows among its own (see
  result-view.md); there is no hold on the panel any more.
- **A grouped deploy calls the plain sandbox API** (`plainSandboxApi` in
  `extension.ts`), not the logging one, which would put up a row of its
  own; `SandboxStore.settle(port)` then updates the Sandboxes view.
- **The connection's REST Daemon lives in the real `~/.mariadb-restd`**
  during the UI test too; the test deletes it again.
