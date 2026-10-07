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
| `editorProtocol.ts` | The messages the panel and its webview exchange; `ready` / `busy` / `cancel` come from `src/webview/dialogProtocol.ts`, shared with New Sandbox. |
| `connectionEditorPanel.ts` | The panel and its message loop. The HTML and webview options are `src/webview/html.ts`'s, as for every webview. |
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
typo in the "Other Connection Options" table from a real option. The
options the editor gives a text field of its own are ONE table,
`DEDICATED_URI_OPTIONS` (option name -> field), which `optionsOf` writes
from and `parseConnectionUri` reads through (`URI_OPTION_FIELDS`, that
table merged with `SSH_URI_OPTIONS`); `UPPERCASE_URI_OPTIONS` names the two
whose values the shell spells in capitals, and `compression-algorithms`
is handled apart as the one list field. Three hand-kept lists used to
have to agree.

The Basic tab's two-column grid starts Caption | Folder, then Host |
Port + Protocol - those two share one cell as a `.field-pair`, in halves -
then User Name | Password (`.password-field`: caption, then a
`.password-row` of the Set / Set New Password button and the state beside
it - `.password-state` takes the rest of the row and wraps; or the box
with Keep Stored Password and "Saved with the connection." under it),
Default Schema | Color.
**Host or socket** is one field: its caption is two small radio buttons,
Host Name/IP left and Socket right (Named Pipe when the host says
`windows`, from `process.platform` in the load message), and its one box
edits `host` or `socket`. Both values are kept, so switching back finds
what was typed; `endpointFields` drops the unchosen one (host and port,
or socket) from what is built, previewed, tested and saved. On the socket
Port is disabled and shown empty; Protocol stays (mariadb:// vs mysql://
still matters), only its `+ssh` options and the SSH tab's tunnel checkbox
are disabled, and switching to the socket drops `+ssh` from the scheme.
A loaded or pasted URI picks the radio button by whether it has a socket.
**Copy URI** sits before Paste (`codicon-copy`; Paste moved to
`codicon-clippy`, the codicons having no paste icon): it posts `copy` with
the box's text - the draft if one is being typed, else the preview, never
a password - and the host writes `vscode.env.clipboard`. The panel's
message switch DISPOSES the panel on an unknown type, so a new message
needs its own case. The icon shows `codicon-check` for `COPIED_FOR_MS`.
**Every button has a `data-tooltip`, none a `title`** (a test walks every
tab and both password states): a title's tooltip is the browser's, about
a second late and inside a webview often never shown (Paste's seldom
was). `Tooltips` (`dialogParts.tsx`, mounted once at the dialog's root)
draws them: ONE `position: fixed` `.tooltip` for the page, placed from the
element's bounding rect under it - over it where there is no room below,
clamped to the window's sides (`placeBelowOrAbove` in
`webview/src/position.ts`, which the overflow popup and the context menu
also place with, each keeping its own gap and margin) - so the scrolling tab body cannot clip it
(a CSS `::after` hung from the button was tried first and would have
been). It shows after `TOOLTIP_DELAY_MS` (350) on hover, at once on
focus-visible, and goes on mousedown / keydown (staying gone until the
pointer reaches another element), on scroll (capturing) and when the
pointer leaves the window. Tests: `webview/test/dialogParts.test.tsx`.
New Sandbox does not mount it yet.
**Focus** goes through `focusNext`, applied by an effect after the render
that shows its target: a radio button picked focuses the box below it
(`endpointInput`, on whichever of the two inputs is shown), and a load
with no caption focuses Caption.
Color's swatches sit 0.3rem in (`.colors` padding): the selected one's
outline reaches 4px past it, and the scrolling tab body clips it. The
dialog starts from what `mariadb.editConnection` gives it - the tree
node's uri, kind, path, caption AND color; leaving one out shows it as
empty (it did, for caption and color, until a test pinned them).

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
  it, or Paste URI (`codicon-clippy`; the host reads `vscode.env.clipboard`:
  a webview cannot read it on a button press), goes through `checkConnectionUri`, the STRICT
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
  hidden past it. The strip of tabs and that body are `TabStrip` and
  `TabBody` in `dialogParts.tsx` (the body owns the ref and calls
  `useScrollFades` itself), shared with New Sandbox, which had a pasted
  copy of both. `useScrollFades` measures after every render AND on the
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
