# Sandbox servers

`sandbox.deploy` asked for a server VERSION: where a server is looked for,
the pinned index it is downloaded from, and how it is checked and unpacked.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Architecture / key decisions

- **`sandbox.deploy` can be asked for a server VERSION** (`server_version`), and
  `sandbox.list_available_versions` says what is on offer. All of it lives in
  `lib/sandbox_servers.py`; the sandbox tools only call `resolve()` and format the answer.
  - **Three places, searched in this order, and the order IS the design**: the PATH (a
    machine that already satisfies the request downloads nothing), then
    `<root>/<version>/` (one directory per version, the version as its name), then the
    published index (download, checksum, extract into 2., then use).
  - **The request may leave levels off**: `11.8.9`, `11.8` or `11`, and each level omitted
    is satisfied by the NEWEST release below it. One rule, not three: `parse_version`
    returns None per missing level and `matches()` treats None as "any". A leading `v` is
    accepted.
  - **The PATH is deliberately NOT held to "newest".** It holds one server; the only
    question is whether it satisfies the request. A machine with 11.8.9 installed must not
    fetch 11.8.10 because a client said `11`. Installed copies and the index DO take the
    newest match.
  - **The index is a static file** (`lib/sandbox_server_versions.json`), not fetched at run
    time: what a plugin version can install is then reproducible and reviewable in a diff,
    and a lookup costs no network. Publishing a new server version means shipping a plugin.
  - **Every package's SHA-256 is pinned there and checked as the body streams past.** This
    ends in running a downloaded executable as a database server. A mismatch is discarded
    with nothing installed — and this is NOT theoretical, it fired for real this session
    (see Gotchas: the v26.9.1 assets were re-uploaded mid-session).
  - **Extraction uses tarfile's `data` filter**, unlike the migrator's hand-rolled zip
    walk: the packages contain symlinks, which a per-member loop would have to handle
    itself, and the filter already refuses absolute paths, `..` and escaping links. Mode
    handling is what the filter gives (owner execute preserved, setuid/setgid stripped),
    verified against a real package — `bin/mariadbd` comes out 0755.
  - **The package's one wrapping directory is stripped** (`mariadb-11.8.9-macos26-arm-64bit-sandbox/`)
    so `<version>/bin/mariadbd` is true of every platform. Done by renaming the sole
    top-level entry, not by rewriting member names.
  - **Staged beside the target, swapped in only once complete and checked**, same shape as
    the migrator's download, including the put-the-old-copy-back on a failed swap.
  - **Install root is NOT `get_data_home()` on Windows.** XDG is a Unix convention;
    `get_sandbox_server_root()` returns `%LOCALAPPDATA%\Programs\mariadb-sandbox-server`
    there, `~/.local/share/mariadb-sandbox-server` elsewhere. Verified live on Windows.
  - **`xattr -cr` on macOS only**, best-effort: without it Gatekeeper refuses a binary that
    arrived over the network, with an error naming none of this. A failure is logged with
    the command to run by hand, never raised — the install is otherwise complete.
  - **Platform keys are Node-style** (`darwin-arm64`, `linux-x64`, `win32-arm64`) because
    the index is shared with tooling that speaks those. `platform_key()` maps every Python
    spelling onto them (`arm64`/`aarch64`, `x86_64`/`AMD64`) in one place.
  - `server_version` and `mariadbd_path` are REFUSED together: both name the server to run.
  - The deploy's message names the source (`found on the PATH` / `already downloaded` /
    `downloaded now`) — two seconds and two minutes deserve different explanations — and,
    for a non-PATH server, the `mariadbd_path` for `sandbox.start` AND the fact that
    shutdown needs `sandbox.kill` (see Gotchas: the shell's `stop` takes no `mariadbdPath`).

- **`sandbox.list_instances` lists the DEFAULT sandbox path only** — by the user's
  decision, so it takes no `sandbox_dir` and is sync with no `ctx` (nothing to
  authorize). Returns `[{port, version, status}]` sorted by port. The shell has no
  listing, so the plugin reads its layout: an instance is `<sandboxDir>/<port>/my.cnf`
  (the shell's own test in `sandbox.version`), which skips the non-numeric boilerplate
  directory. The path is `shell.options["sandboxDir"]` — the shell's
  `default_sandbox_base_dir()` reads the same option. `version` comes from
  `sandbox.version(port)` (None when undeterminable); `status` is `running` when
  localhost:port accepts a TCP connection, the same check `stop`/`delete` make. An optional
  `port` asks about that one instance alone - no directory walk, one version
  lookup, one probe - for the extension to refresh a single row; the answer
  is still a LIST (one entry, or `[]` when there is no sandbox there), so a
  caller reads it the same way. An older plugin drops the undeclared argument
  and answers with every instance, which is why the extension picks the port
  out of the answer rather than trusting it to be the only entry. Tested
  in-process in `tests/unit/test_sandbox.py` against stand-in `sandbox`/`shell`
  globals, and verified by hand against two real instances (one running, one stopped).

- **`sandbox.deploy` takes `mcp_access` in `--gui` mode ONLY** (default
  True = the shared `mcp` list, as before; False = the extension's own `gui`
  list, still in `/Sandboxes`). Without `--gui` it is not ADVERTISED:
  `_register_deploy` trims it from the function's `__signature__` (which
  the SDK builds the schema from; `functools.wraps` in `tool_registrar`
  carries it) and its `{mcp_access_doc}` line from the docstring, and the
  function keeps the parameter at its default - an agent's sandbox must be
  one the agent can open. `test_deploy_offers_mcp_access_only_with_gui`
  checks both advertised schemas over stdio. `sandbox.delete` now removes
  the sandbox's connection from BOTH lists. Verified live: deploy with
  `mcp_access=False` lands in `gui` / `/Sandboxes`, delete removes it.

## Files that matter

- lib/sandbox_servers.py -> EVERYTHING about getting a server of a requested version:
  `load_index`/`index_path`/`SUPPORTED_INDEX_VERSION`, `platform_key`/`require_platform_key`,
  `available_versions`, `_matching_series`/`_series_versions`/`_series_text`/`_package_of`/
  `_sort_key`, `parse_version`/`matches`/`_VERSION_PATTERN`, `find_server_binary`/
  `installed_versions`/`path_server_version`, `_download_package`/`_extract_package`/
  `clear_quarantine_flags`, `install`, `resolve`, `ResolvedServer` +
  `SOURCE_PATH`/`SOURCE_INSTALLED`/`SOURCE_DOWNLOADED`, `WORK_PREFIX`, `DOWNLOAD_TIMEOUT`.
  Raises `mysqlsh.Error` (the sandbox group registers through `tool_registrar`).
  **100% covered — keep it that way.**

- lib/sandbox_server_versions.json -> the published index: `sandboxServerIndexVersion` 1,
  then `serverVersions[]` of `{major, minor, latestPatch, patches[{"<patch>": [{os, url,
  sha256sum}]}]}`. The nesting is awkward on purpose (diff-friendly as releases are added)
  and is flattened once, in `_series_versions`. **The checksums track the published
  assets** — if the release is re-cut they go stale and the e2e test is what says so.

- tests/unit/test_sandbox_servers.py -> the 103 tests for the above. NO network: `urlopen`
  is stubbed with a locally built `.tar.gz` and `load_index` with an index whose checksums
  are of it (`_stub_index`/`_stub_download`/`_package_bytes`/`_FakeResponse`). The
  `server_root` fixture patches BOTH `get_sandbox_server_root` and `get_sandbox_server_path`
  — patching one leaves the other answering out of the real home. `FAKE_VERSION` is
  `10.6.1`, deliberately not a version the shipped index carries. Weighted to the failure
  paths: bad checksum, no server in the package, an entry escaping the target, a failed
  download keeping the installed copy, a failed SWAP putting it back. The deploy-message
  tests drive the tool IN-PROCESS through a `_ToolRecorder` + `asyncio.run` rather than
  paying for three real server starts to read a string back. One `e2e` test really
  downloads and really deploys.

## Gotchas / things not to repeat

- **A PUBLISHED RELEASE ASSET CAN CHANGE UNDER YOU, and the pinned checksum is the only
  thing that notices.** Mid-session, all twelve v26.9.1 sandbox packages were re-uploaded:
  the same URL that had matched `3cf3848f…` at 18:44 served `ae77f8d5…` (and a different
  size, 26,266,593 -> 28,769,442) at 20:00. The `--e2e` test failed on the checksum, which
  is exactly what it is for. **Do NOT silently re-pin to whatever is published now** —
  trusting a fresh upload is the user's decision, and if the release is still being re-cut
  the new pins go stale again within the hour. Report it and wait to be handed the sums.

- **`curl -w` WRITES TO STDOUT, so piping it into `shasum` corrupts every digest.** The
  first sweep of all twelve packages reported 12/12 mismatched with digests that were
  pure artefact (`e528cb67…` where a clean download gives `ae77f8d5…`). Nearly reported it
  as "the assets are changing between my own downloads". Verify with
  `curl -sL "$url" | shasum -a 256` and NO `-w`; if a byte count is wanted, use `-o` to a
  file and stat it.

- **The shell's `sandbox.stop` accepts NO `mariadbdPath`** — probed option by option
  against shell 26.9.1: `deploy`/`start`/`vendor`/`version` take one, `stop`/`kill`/
  `delete` do not (`stop` takes sandboxDir+password+timeout, `kill` and `delete` take
  sandboxDir alone). Its error message says "Use the 'mariadbdPath' option" anyway, which
  is a shell-side bug. Consequence: a sandbox deployed on a DOWNLOADED server cannot be
  stopped gracefully on a machine with no server on the PATH; `sandbox.kill` is the way,
  and the deploy message now says so. **The MCP wrappers are already faithful** — each
  offers exactly the options the shell accepts, so do not "fix" this by adding a
  `mariadbd_path` to `sandbox.stop`; the shell will reject it at Argument #2.
  **SUPERSEDED IN PART as of shell 26.9.2**, which this plugin now declares
  (`lib/general.py VERSION`): `sandbox.stop` no longer depends on a server being
  on the PATH, so the "cannot be stopped gracefully" consequence is gone. The
  comment at `lib/sandbox_functions.py:213` records it, and the deploy message
  now only explains what `sandbox.start` needs. The option surface itself is
  unchanged — `stop` still takes no `mariadbdPath`, so the last two sentences
  above still hold.

- **Sandbox port required** — never None/omit ("Argument #1 is expected to be an integer").

## Next steps

1. **DECIDE: should `sandbox.deploy` / `sandbox.delete` survive an unusable secret store?**
   Found on Windows, NOT fixed, because it is pre-existing code outside the feature and
   changing it is the user's call. Both do their real work and THEN raise — `deploy` on
   `config.store_connection`, `delete` on `config.resolve_connection_uri` (it was
   `list_connection_uris` before the scheme change) — so a successful
   deploy is reported as a failure while leaving a server running, and the client is never
   told the port. The download feature makes this far likelier to bite: machines with no
   MariaDB are exactly the ones that have not run `mcp.setup`. Options if the user wants
   it: catch around the config call and append "the sandbox is up but could not be
   registered" to the success message.
