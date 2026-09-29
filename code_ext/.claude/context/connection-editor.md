# The connection editor

The dialog that adds and edits a connection: its files, its tabs and
what it deliberately leaves out, and the rules its URI box follows.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Split out of
[connections.md](connections.md), which has the connections it edits - the
several open on one URI, the Connections view, folders and the default.


`src/connections/` holds it, split so that only the panel needs VS Code:

| File | Purpose |
| --- | --- |
| `connectionUri.ts` | Fields <-> URI, and the allow-list of URI options. Pure. |
| `connectionStore.ts` | Add / edit / delete / test, over `IMariaDbApi`. Pure. |
| `editorProtocol.ts` | The messages the panel and its webview exchange. |
| `connectionEditorPanel.ts` | The panel, its HTML and the message loop. |
| `webview/src/ConnectionEditor.tsx` | The dialog itself. |

It is modelled on the MySQL Shell extension's `ConnectionEditor`: the same
tabs (Basic, SSL, SSH, Advanced) in the same order, with the same captions
where the setting is the same one. **What is missing is missing on purpose** -
a connection here is stored as a URI and nothing else, so a setting that
cannot be written into one cannot be offered. That rules out the OCI/MDS tabs,
`sql-mode`, the HeatWave check, and the two SSH passwords (`ssh-password`,
`ssh-identity-file-password`), which the shell keeps out of a URI deliberately.
`URI_OPTIONS` is `uri_connection_attributes` plus `ssh_uri_query_attributes`
from the shell's `mysqlshdk/libs/db/utils_connection.h`, and is what tells a
typo in the "Other Connection Options" table from a real option.

Five things about it are load bearing:

- **The scheme is a real field and always written out.** `db.list_connections`
  reports `scheme://user@host:port` from MariaDB Shell 26.9.3 on, and the
  scheme is part of what identifies a connection to the server - `mariadb://`,
  `mysql://`, `mysqlx://` and the `+ssh` forms are five different connections.
  `emptyConnectionFields()` therefore starts on `DEFAULT_SCHEME` (`mariadb`),
  `parseConnectionUri` keeps whatever scheme it reads (lowercased, since they
  are case-insensitive and the shell emits them lowercased) and defaults a URI
  with none - one stored before 26.9.3 - to the same. `withDefaultScheme()` is
  the textual fill-in for comparing a URI written down by an older version of
  the extension against one listed now; the default-connection setting is the
  one place that matters, and `ConnectionsModel.getRoots` puts both sides
  through it or the default connection quietly loses its marker.
- **The SSH tab is a view onto the scheme.** There is no option that turns a
  tunnel on - `mariadb+ssh://` is the whole of it - so the tab's "Connect
  through an SSH tunnel" checkbox is the Protocol dropdown by another name
  (`withSshTunnel` keeps the base scheme and adds or removes `+ssh`). The
  `ssh-*` fields are hidden and NOT emitted while it is off, because the shell
  refuses an `ssh-*` option on any other scheme - carrying one over would make
  the connection unsaveable rather than simply untunnelled. The authority of a
  `+ssh` URI is the DATABASE; `ssh-host` names the bastion, and left out, the
  database host IS the SSH host and the tunnel forwards to loopback there.

- **`buildConnectionUri` emits the shell's own canonical spelling.** Every
  expectation in `connectionUri.test.ts` was produced by running the fields
  through the real `shell.unparse_uri`, then feeding the result back through
  `parse_uri`/`unparse_uri`, which returned it unchanged. Options are sorted
  because the shell's encoder sorts them; two URIs naming one connection must
  come out identical or the server sees two connections.
- **A password is never in the URI and never leaves the server.** Nothing can
  read a stored password back - no tool returns one - so the host sends the
  webview `hasStoredPassword`, not a value. `password: undefined` means "keep
  what is stored" and is NOT the same as `""`, which is a real password for an
  account that has none.
- **Editing is a re-key, not an update in place.** A connection is keyed by
  its URI and by which list it is in, so changing the host or the MCP checkbox
  moves it. That is what `db.update_connection` is for: it moves the secret
  server-side, so an edit does not make the user retype the password.
- **The URI is shown, and editable, above the tabs.** It shows
  `previewConnectionUri(fields)` - the fields written out UNVALIDATED, so a
  new connection reads `mariadb://@localhost:3306` rather than nothing -
  with `buildConnectionUri`'s complaint as a muted note beneath. Typing into
  it, or Paste URI (the host reads `vscode.env.clipboard`: a webview cannot
  read it on a button press), goes through `checkConnectionUri`, the STRICT
  counterpart of `parseConnectionUri`: a sound URI replaces the fields, an
  unsound one is kept as a draft with the fields left as they were, and the
  problem carries a `start`/`end` so the editor marks the offending part and
  selects it. While a draft does not parse, Save / Create and Test
  Connection refuse and select the problem instead of posting - they would
  otherwise quietly act on the older fields. Editing any field drops the
  draft; a sound draft gives way to the canonical spelling on blur. A
  password in a typed or pasted URI is MOVED: `checkConnectionUri` returns
  it separately (decoded; `dba:@db` is the empty password, not none), never
  in `fields`, and the editor puts it in the Password field and drops the
  draft at once, so the box stops showing it in the clear.
- **The tab body says when it scrolls.** `.tab-body` is wrapped in
  `.tab-scroller`, which lays a gradient (`.scroll-fade.top` / `.bottom`,
  into `--vscode-editor-background`) over an edge only while content is
  hidden past it. `useScrollFades` measures after every render AND on the
  frame after, on scroll, on the area or its content resizing (content
  re-watched by a MutationObserver when a tab switch swaps it), on window
  resize, on fonts arriving, and on pointerenter as a last resort. That many
  because a webview is sized and styled after its first render: with only
  render + scroll + a ResizeObserver on the first content, a small window
  showed no fade until the first scroll. Not reproduced outside VS Code -
  plain Chrome under CDP showed the fade with late CSS, a hidden body and a
  late resize alike. The fades' `right` is set inline to the measured
  scrollbar width (`offsetWidth - clientWidth - borders`): 0 under macOS's
  overlay scrollbars, where a fixed inset left an unfaded strip. There is no subtitle under the title any more - the
  URI box says which connection is open.
- **Saving does not verify**, as in the original. Test Connection is its own
  button; a server that is down must not stop its connection being configured.

The editor is **its own vite build** (`vite.editor.config.ts`), not a second
entry beside the result view. One build with two entries makes Rollup hoist
what they share into a common chunk, which each entry pulls in with a static
`import` - and a webview's `script-src 'nonce-...'` does NOT extend to a
module the entry imports, so that chunk would be refused and the view would
come up blank. One entry per build has nothing to hoist.
