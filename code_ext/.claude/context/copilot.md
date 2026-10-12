# GitHub Copilot

What the extension gives Copilot - and any other chat in VS Code - so it
can work with MariaDB: the MariaDB agent skills, and the MariaDB Shell's MCP
server. This is the VS Code harness of the
[MariaDB AI plugins](https://github.com/mariadb-corporation/ai-plugins),
which ship the same skills and server to Claude Code, Codex, OpenCode and Pi.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). How the shell is found
and installed is in [shell-and-mcp.md](shell-and-mcp.md).

## The skills

- **Contributed statically**, through `contributes.chatSkills` in
  `package.json`: one `{ path, when }` entry per skill,
  `skills/<name>/SKILL.md`, under `when: config.mariadb.copilot.enabled`.
  VS Code has no API to add skills at run time, which is why the list is
  generated into the manifest. Its two rules: the path must be inside the
  extension, and the folder's name must equal the `name` in the
  `SKILL.md` frontmatter - a skill that breaks the second is dropped
  without a word.
- **Vendored from the newest ai-plugins release**, not from `main`: the
  extension then ships the skills every other harness has, never one that
  is merged but unreleased. The source is that release's
  `claude/dev-plugin/skills` - the full `dev` set (85 skills at v26.10.2).
- **Committed.** A skills update is a diff to review, a build without the
  network still has every skill, and CI checks what the branch holds.

### `tools/syncAiSkills.mts` (`npm run sync:skills`)

Run by `npm run build`, so by `vscode:prepublish` and so by `vsce package`.

1. Lists the ai-plugins releases (`GET /repos/.../releases`) and takes the
   highest `vX.Y.Z` that is not a draft. Prereleases count: every
   ai-plugins release so far is one, so `releases/latest` finds nothing.
2. If `skills/skills-source.json` already records that tag, nothing is
   downloaded.
3. Otherwise it downloads the tag's tarball from codeload, unpacks it with
   the system `tar`, checks every skill (a `SKILL.md`, named after its
   folder), copies them into `skills.new/` with a fresh
   `skills-source.json` (tag, date, and the release's own
   `skills-source.json` as `upstream`) and `README.md`, and only then
   swaps it in for `skills/`. A skill dropped upstream goes; a failed
   download or a misnamed skill leaves `skills/` as it was.
4. Rewrites `contributes.chatSkills` from what is in `skills/`, if it
   differs.

A failure - no network, a rate limit, a broken release - keeps the skills
already there with a warning, unless `--strict` is passed or there are none
yet. `SKIP_SKILL_SYNC=1` skips the check (CI sets it); `GH_TOKEN` or
`GITHUB_TOKEN` lifts the API's rate limit. The script runs on Node's own
TypeScript support, so it uses only erasable syntax and the built-ins - not
even `src/`, whose `.ts` files make Node warn about their module type; it
is type checked (`tsconfig.json` includes it) and tested in
`src/test/tools/syncAiSkills.test.ts`, which also holds the committed
`skills/` against `chatSkills`. `tools/**` is in `.vscodeignore`; `skills/`
ships.

## The MCP server

`src/copilot/mcpServerProvider.ts`, registered in `activate()` under the id
`mariadb.mcpServer` that `contributes.mcpServerDefinitionProviders`
declares. VS Code lists the server as **MariaDB**.

- **`provideMcpServerDefinitions`** is cheap and never installs: it lists
  one `McpStdioServerDefinition` on the bare `mariadb-shell` (`.exe`), the
  same every time, and nothing while the setting is off. Listing the real
  path once it is known would change the definition after the first start,
  which VS Code takes as a reason to offer a restart.
- **`resolveMcpServerDefinition`** runs just before VS Code starts the
  server. It is where the shell is looked up and, if there is none,
  installed - with the same progress notification as for the extension's
  own server - and where the real binary and arguments are filled in. A
  failure is thrown, which VS Code reports to the model; the *MariaDB*
  output channel has the detail.
- **Agent mode, no `--gui`** (`buildMcpServerCommand(location, "agent")`,
  `AGENT_MCP_SERVER_ARGS`, which the extension's own `MCP_SERVER_ARGS`
  extend with `--gui`): Copilot is an
  autonomous client, so it gets the server the AI plugins' launchers start.
  It keeps the allowed-path list and asks before going outside it, opens
  only the connections with **MCP access** ticked (the `mcp` kind, marked
  `MCP` in the Connections view), and serves none of `db.add_connection`,
  `db.update_connection`, `db.delete_connection` or `db.test_connection`.
- **`onDidChangeMcpServerDefinitions`** fires when `mariadb.copilot.enabled`
  changes. A shell upgrade needs no event: it only happens after an
  extension update raised `MINIMUM_SHELL_VERSION`, which reloads the window.

Copilot's server is a process of its own, beside the extension's `--gui`
one; VS Code owns it, and Restart MCP Server does not touch it.

## One lookup for both servers

`ShellResolver` (`src/shell/shellResolver.ts`) wraps `ensureShell()`: one
lookup at a time, joined by whoever asks meanwhile, each told if the
installer runs. Without it the Connections view and Copilot, both reaching
for the shell when a window opens, would each run the installer into the
same directory - the race `ServerStarter` was written to close for the
extension's own callers. The "MariaDB Shell was installed" message is shown
there too, so it appears whichever server asked.
