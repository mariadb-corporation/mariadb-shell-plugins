# Sandboxes

The Sandboxes view below the Connections view, its Start / Stop / Delete
buttons, and the New Sandbox dialog.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The server side -
`sandbox.list_instances` and the rest of the `sandbox.*` tools - is in the
MCP plugin's own `context/sandbox.md`.

| File | Purpose |
| --- | --- |
| `src/mcp/sandboxApi.ts` | `ISandboxApi` / `SandboxApi`: the six `sandbox.*` tools used, typed, and their timeouts. |
| `src/tree/sandboxesTreeProvider.ts` | The view: rows, the welcome state, and the actions with their spinner. |
| `src/sandboxes/sandboxStore.ts` | `SandboxStore`: the instance and version lists, read once and kept, and the calls that change them. |
| `src/sandboxes/sandboxActivity.ts` | `createLoggingSandboxApi`: every `sandbox.*` call as a General Action. |
| `src/sandboxes/sandboxFields.ts` | The dialog's fields: defaults, the port suggestion, validation, the deploy options. Pure. |
| `src/sandboxes/sandboxProtocol.ts` | The messages the dialog and its host exchange. |
| `src/sandboxes/sandboxEditorPanel.ts` | The dialog's panel, HTML and message loop. |
| `webview/src/SandboxEditor.tsx` | The dialog itself; `sandbox.tsx` is its entry. |
| `webview/src/dialogParts.tsx` | `Field` and `useScrollFades`, moved out of `ConnectionEditor.tsx` so both dialogs share them. |

## Decisions

- **The lists are read once and kept** (`SandboxStore`), because listing
  walks the sandbox directory and probes every instance, which is slow, and
  the view redraws twice per action. `sandbox.list_instances` is called:
  the first time the view needs it; on the toolbar's **Refresh**
  (`mariadb.refreshSandboxes` -> `SandboxesTreeProvider.reload()` ->
  `invalidate()`). Nothing else lists them all. A successful start, stop
  or delete patches the kept list. Where only the server knows - the new
  sandbox's version after a deploy (success OR failure, a late failure may
  leave one behind), or where a FAILED start / stop / delete left it -
  `refreshInstance(port)` asks about that one sandbox
  (`sandbox.list_instances(port=...)`) and patches it in: updated, added in
  port order, or taken out. It picks the port out of the answer, since an
  older plugin ignores the argument and lists everything. Only if that
  lookup fails too is the whole list dropped (`#settle`), and it never
  masks the original failure.
  A listing in flight is never patched over (`#kept`), a failed one is not
  kept, and concurrent callers share one. `sandbox.list_available_versions`
  is kept for good once read - the server reads a file its plugin ships -
  so the dialog lists it once per extension session. `refresh()` redraws
  from what is kept; only `reload()` re-reads. Known cost: a sandbox an
  agent deploys shows at the next Refresh.
- **Every `sandbox.*` call is a General Action** when
  `mariadb.actions.logAllCalls` is on: `createLoggingSandboxApi` wraps the
  API in `extension.ts`'s `sandboxApi()`, using the same
  `createGeneralWatcher` the `db.*` wrapper does (extracted from
  `createLoggingApi` for this). The deploy's password shows as `***`.

- **Only the default sandbox path.** `sandbox.list_instances` lists
  nothing else (the user's decision, made on the plugin side), so nothing
  in the extension names a `sandbox_dir` - not the dialog, not start, stop
  or delete. A sandbox deployed elsewhere would vanish from the view.
- **A separate API, not more of `IMariaDbApi`.** `SandboxApi` sits on the
  same `IToolCaller`, and `McpSession` builds it beside `MariaDbApi` and
  drops it with it (`session.sandboxApi`). Adding six methods to
  `IMariaDbApi` would have meant touching `createLoggingApi`, the
  manager and every fake for tools none of them use. `extension.ts`'s
  `sandboxApi()` starts the server the same way the connection manager's
  getter does.
- **Per-call timeouts.** `IToolCaller.callTool` takes an optional
  `timeoutMs`, which `sdkConnector` passes to the SDK. The SDK's default
  is 60 s - exactly how long the shell waits for a server to start or
  stop, and far short of a deploy that downloads a server. `DEPLOY_TIMEOUT_MS`
  is 30 min, `LIFECYCLE_TIMEOUT_MS` 3 min.
- **Start takes no server binary.** The start script written at deploy
  time records the binary it was built with, downloaded ones included, so
  `sandbox.start(port)` alone starts it on the right version.
- **Delete stops a running sandbox first** (the shell refuses otherwise),
  and says so in its modal confirmation. Stop and Delete also close
  whatever the extension has OPEN on the sandbox's registered connection
  first (`disconnectSandbox`, comparing through `withDefaultScheme`), so a
  session is not left to fail on its next use.
- **A deploy or delete re-reads the connection list.** The plugin registers
  `mariadb://root@127.0.0.1:<port>` in `/Sandboxes` on deploy and removes it
  on delete, server-side, so `connectionsChanged()` invalidates the cached
  list and redraws the Connections view.
- **Rows.** Label `localhost:<port>`, description `<version> · <state>`,
  `id` `sandbox:<port>`. `contextValue` `mariadbSandbox.<running|stopped|busy>`:
  Start shows on stopped, Stop on running, Delete on both, nothing on
  busy. An action under way puts `loading~spin` and `starting…` /
  `stopping…` / `deleting…` on the row; a second action on it is refused.
  The view is listed again after every action, success or failure.
- **Welcome content** switches on `mariadb.sandboxesView` (looking /
  installing / failed / listed), worked out as the Connections view's is.
  A failed listing is LOGGED, not notified: the welcome content says so
  with Show Log and Retry, and the Connections view has already notified
  when the server itself failed. Both views show the startup busy bar.
- **The dialog** is modelled on the connection editor: the header with
  a description under the title (`.editor-description`, a little larger
  than a hint), the connection it will register as TEXT (`<code
  class="sandbox-uri">`, following the port - not an input, which would
  say it can be typed into) with an **Allow MCP access** checkbox beside
  it - the line is a `.grid` of the fields' two columns (caption across
  both), so the checkbox starts where Server Version and Confirm Root
  Password do; the tab body has no side padding, so the columns match
  (a classic, non-overlay scrollbar in the tab body would narrow them
  slightly) (`ISandboxFields.mcpAccess`, ticked by default as an agent's deploy
  is; sent as `mcp_access` always, which only a `--gui` server serves - an
  older plugin drops it and files the connection in the shared list), Basic (port, server version, root password and its confirmation) and
  Advanced (**Allow root Access From**, default `127.0.0.1` - this machine
  only, where the shell's own default is `%` - so it is always sent; server
  ID, startup timeout, SSL, server options), Cancel and
  Create. The host suggests the first port from 3310 UP BY ONE that no
  sandbox is on and no configured connection to this machine names
  (`localConnectionPorts`: `localhost`, `127.0.0.1` and `::1` in any case
  are one machine; no port means 3306; `+ssh` connections are left out,
  since their `localhost` is the SSH host's; socket connections too). The
  connections come from the Connections view's cached list, so opening
  the dialog lists nothing. A connection's port is only NOT SUGGESTED - its
  server may be long gone - while a sandbox's is refused. **Server
  Version is a typed box with a list** (`webview/src/ComboBox.tsx`), NOT
  an `<input list>`: a datalist only offers entries matching what the box
  holds, so a box starting on `12` would never offer the PATH. The list is
  `serverVersionChoices`: every listed version newest first (`newestFirst`
  compares numbers, so 11.10 > 11.9), then `SERVER_ON_PATH` ("Server on the
  PATH") last. The box starts on the HIGHEST full version
  (`defaultServerVersion`, `12.3.2` today). (A bare major version, `12`,
  was offered and pre-filled for one round and dropped as harder to read.)
  It still accepts a typed `12`, `11.8`,
  `11.8.9` (optional `v`) or "Server on the PATH" in any case, which
  `sandboxDeployOptions` sends as no version. Anything else - empty
  included - is marked AT ONCE, not after a touch like the other fields,
  and disables Create (`serverVersionProblem`, its message as the button's
  title). A failed listing of the versions leaves the PATH as the one
  choice and the starting value; a failed listing of the connections just
  suggests less carefully. A problem shows under its field once the field
  is typed into, or for all on Create, which also switches to the tab it
  is on. The host checks the fields again before deploying.
- **The dialog is not reloaded when asked for again**, unlike the
  connection editor: a deploy may be running in it. A deploy that fails
  after the dialog was closed is notified instead.
- **The password is always sent**, empty included: the shell refuses a
  deploy without one. **Confirm Root Password** sits beside it
  (`passwordConfirmation`, the grid's second column, the note spanning
  both under them); a mismatch - compared as typed, spaces included - is a
  `sandboxFieldProblem` on the confirmation, shown like the other fields
  (once typed into, or on Create) and refused again by the host. The
  confirmation crosses to the host with the fields but is never sent to
  the server.

## Known gaps

- **Needs an unreleased shell.** `sandbox.list_instances` is new in this
  repo's `mcp_plugin`; no released shell bundles it, and an older one fails
  the listing - the view then shows its failed welcome content. Raise
  `MINIMUM_SHELL_VERSION` once a release carries it, as with folders.
- **Stop on Windows sends no password.** The shell uses the root password to
  request a shutdown there; the extension cannot read the stored one back,
  so a Windows stop relies on the shell's fallback. Not tried on Windows.
- **The deploy shows no download progress** - the server does not report
  any. The view's busy bar and the dialog's note are all there is.
- Verified end to end (list, deploy, stop, start, stop, delete) by driving
  `SandboxApi` through the real SDK connector against the dev shell with
  this repo's plugin. The views themselves have not been clicked through
  in a running VS Code.
