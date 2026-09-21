# The shell, its bundled Python and the MCP SDK

Where the code that actually runs lives, which SDK the build has, and how
both of those have moved under the plugin before.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Project

The shell's bundled Python — the one that matters for every "which version does this
behave like" question — is
`/Users/mzinner/git/mariadb-shell/build/lib/mariadb-shell/lib/python3.14/site-packages`.
Read the SDK and uvicorn sources THERE, not upstream. (Ask the shell itself rather than
`find /`: `mariadb-shell --py -f <script printing module __file__>`.)
**There are TWO dependency trees** in the shell build: that site-packages one, which is
what actually runs, and `build/bundled-python-deps/`, a staging copy. As of this session
they hold the same versions (mcp **2.1.1**, uvicorn 0.52.1) and the files that matter are
byte-identical — but only the site-packages one is authoritative, so always get the path
from the running shell rather than from a filesystem search. (`/System/Volumes/Data/...`
hits are the same files through the macOS firmlink, not a third copy.) Note that
site-packages accumulates STALE `*.dist-info` directories, because the staging copy is
overlaid rather than installed: `mcp-2.0.0.dist-info` still sits beside
`mcp-2.1.1.dist-info` there. Harmless — diffing the two `RECORD`s showed zero 2.0.0-only
files left on disk, and `importlib.metadata.version('mcp')` answers 2.1.1 — but do not
read a version off a directory listing.

The shell's env vars are `MARIADB_SHELL`, `MARIADB_SHELL_USER_CONFIG_HOME` and
`MARIADB_SHELL_TERM_COLOR_MODE` — the pre-rename `MYSQLSH*` names are GONE from all
three plugins' runners and test helpers. `run_tests.py` exports `MARIADB_SHELL` and
`tests/unit/helpers.py shell_binary()` reads it; keep those two in sync or the suite
silently runs against whatever `mariadb-shell` is on PATH.

## Architecture / key decisions

- MCP server built with **MCPServer** (`mcp.server.mcpserver`, the MCP Python SDK 2.x
  successor of 1.x's `mcp.server.fastmcp.FastMCP` — NOT the standalone `fastmcp` v2).
  `requirements.txt` pins `mcp >= 2.0.0, < 3.0.0`; on the 1.x API every server-side import
  here fails. `mcp`, `msm_plugin`, `mrs_plugin`, and the `sandbox` global are imported
  lazily so plugin load never hard-fails.

- **SDK 2.x API notes**: `MCPServer(name)` takes NO `host`/`port` — they are transport
  options passed to `run(transport=..., host=..., port=...)`, so `build_mcp_server()` takes
  only `function_groups`. The low-level server is `_lowlevel_server` (1.x: `_mcp_server`).
  Client side, `mcp.client.streamable_http.streamable_http_client` (1.x:
  `streamablehttp_client`) yields a 2-tuple `(read, write)` — the third `get_session_id`
  element is gone. The result models moved to **snake_case attributes with camelCase wire
  aliases**: `result.is_error` / `result.structured_content` (1.x: `isError` /
  `structuredContent`). The wire format did NOT change, so this is attribute access only —
  but `getattr(result, "structuredContent", None)` silently returns None instead of
  raising, which is exactly how `tool_payload` degraded unnoticed to its text-block
  fallback. `stdio_client`, `StdioServerParameters`, `ClientSession`,
  `server.tool(name=...)`, `stdio_server(stdout=)` and `ctx.elicit(message=, schema=)` are
  unchanged, as is `types.ElicitResult(action=, content=)`.

## Current state

- **Feasibility findings about the shell's bundled Python** (established by experiment
  before any of the above was written):
  - The shell ships a REAL CPython binary at
    `<shell>/lib/mariadb-shell/bin/python3.14` (33KB, `Python 3.14.6`), with `venv`,
    `ensurepip` and `pip` importable.
  - `mariadb-shell --pym venv .venv` produces a fully standard venv: `pyvenv.cfg` `home`
    points at that binary, `bin/python3.14` symlinks to it, `sys.prefix != sys.base_prefix`
    inside it, and `bin/pip` is real pip 26.1.2.
  - **`mariadb-shell --pym <module>` does NOT see a venv, even an activated one.**
    `VIRTUAL_ENV` is visible in the environment but `sys.prefix` stays the shell's own and
    the venv's site-packages never joins `sys.path`. `PYTHONPATH=<venv>/lib/python3.14/
    site-packages` DOES bridge it (verified) but is not what this uses — the shell's
    Python is the venv's BUILDER, the venv's own python3 is the RUNNER.
  - `useWizards` is TRUE under `--py -e` with piped stdin, which is why the interactive
    setup can be driven non-interactively at all.

- Previous session: **migrated the plugin from MCP SDK 1.x to 2.0.0** (the shell's bundled
  Python now ships 2.0.0; `requirements.txt` had `mcp >= 1.2.0` with no upper bound, so the
  major bump was picked up silently and 8 of 17 tests failed —
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` killed the server subprocess
  at import, which surfaced as `MCPError(-32000, 'Connection closed')`). Changes:
  `mcp.server.fastmcp.FastMCP` -> `mcp.server.mcpserver.MCPServer` (server.py, plus the
  `Context` import in msm_functions/sandbox_functions), host/port off the constructor and
  onto `run()`, `_mcp_server` -> `_lowlevel_server`, `streamablehttp_client` ->
  `streamable_http_client` (2-tuple), and `.isError`/`structuredContent` ->
  `.is_error`/`structured_content` at 31 assertion sites + `tool_payload`.

## Gotchas / things not to repeat

- **WHETHER THE SDK SWALLOWS A TOOL EXCEPTION'S MESSAGE IS VERSION-DEPENDENT, and it
  flipped at 2.1.0. `tool_registrar` is LOAD-BEARING again — do not delete it a second
  time.** The history, because this has now been got wrong in both directions:
  - **1.28.x and 2.0.0 APPEND the message** whatever type was raised: `Tool.run` ends in
    one `except Exception` raising `ToolError(f"Error executing tool {self.name}: {e}")`
    and `_handle_call_tool` puts `str(e)` in the content block. So against those the
    wrapper changed nothing a client saw, which is why it was deleted (d530e97d) —
    measured at the time, correctly, for the SDK then in the build.
  - **2.1.0 masks it again, deliberately**, sorting a failure into three buckets in
    `mcp/server/mcpserver/tools/base.py`: `ToolError` and `ResourceError` keep their
    message, `MCPError` becomes a JSON-RPC protocol error, and **everything else is
    re-raised as `UnexpectedToolError(f"Error executing tool {self.name}")`** — a crash,
    logged server-side with its traceback and withheld from the client. The docstrings of
    the new `Unexpected*Error` classes say so outright; read them before theorising.
  - `mysqlsh.Error` is in that third bucket, so with the wrapper gone every anticipated
    refusal in db/msm/sandbox reached the model as a bare "Error executing tool <name>".
    **That is what broke CI on PR #19** (run 34115890193): exactly three tests, the ones
    that assert a tool's own sentence — `test_db_connect_execute_and_close`,
    `test_stdio_elicits_and_declines_new_path`,
    `test_sandbox_dir_outside_allowed_paths_is_rejected`. Reverted whole in e7cd1c85.
  - **Reproduced locally, which is the only reason it is nailed down**: the same three
    fail at d530e97d and pass at the revert once the build's bundled SDK is 2.1.1 (see the
    bundled-deps cache trap below — a rebuild alone does NOT update it).
  - The conversion is portable, not a patch for 2.1: on 1.28.x/2.0.0 the payload is
    byte-identical either way, because the wrapper raises `ToolError(str(exc))` and
    `str(ToolError(s)) == s`.
  - **`ToolError`, `ResourceError` and `MCPError` now pass through unconverted** (they
    were not before, and `MCPError` mattered: converting one downgrades a protocol error
    to a tool failure). `tests/unit/test_tool_registrar.py` pins all of it directly with
    a fake server — six tests, no server started; two of them fail if the passthrough is
    reduced back to `except ToolError`.
  Consequence for tests: **"Error executing tool" is ALWAYS in the payload**, so
  `assert "Error executing tool" not in payload` is not a test of anything — it was
  written that way once and failed immediately. Assert the tool's own sentence instead.

- **REBUILDING THE SHELL DOES NOT UPDATE ITS BUNDLED PYTHON PACKAGES, and that is how a
  local build can differ from a CI shell of the SAME version.** `build/bundled-python-deps/`
  is a cache keyed ONLY on the requested package LIST, recorded in
  `build/bundled-python-deps.stamp` (`CMakeLists.txt`, ~line 1704). The stamp reads
  `certifi;pyyaml;antlr4-python3-runtime;mcp` — `mcp` is UNPINNED, so as long as that list
  is unchanged pip never re-runs and whatever `mcp` resolved to months ago is reused
  forever. A rebuild moved the local shell 26.8.0 -> 26.9.0 while leaving mcp at 2.0.0,
  and CI's 26.9.0 tarball (built with a cold cache) had 2.1.1: **same shell version, two
  different SDKs, and only CI saw the failure.** To force a re-resolve:
  `rm -rf build/bundled-python-deps build/bundled-python-deps.stamp` and rebuild. Do that
  before concluding "it passes locally" about anything SDK-shaped, and check the version
  the way the paragraph at the top says (ask the running shell, not a directory listing).

- **NEVER import the MCP SDK at the module scope of anything the plugin imports
  EAGERLY.** `from mcp.server.mcpserver.exceptions import ToolError` alone loads ~110
  `mcp.*` modules, `mcp.client.stdio` among them, and that module binds
  `stdio_client`'s `errlog=sys.stderr` as a DEFAULT at import time — to the shell's
  `mysqlsh.shell_stderr`, which has no usable `fileno()`. Measured both ways: importing
  `mcp_plugin` loads **zero** `mcp.*` modules today, and one eager `ToolError` import
  loads all 110. `migrator_functions` gets to import it at module scope ONLY because
  `lib/server.py` resolves registrars lazily; `lib/__init__.py` must never import
  `migrator_functions` either. `test_loading_the_plugin_imports_no_mcp_sdk_module` pins
  it, in a SUBPROCESS — an in-process check could only ever pass, since the test
  process imports the SDK itself.

- **OBSOLETE AS OF SDK 2.0 — do not act on the old note.** Under mcp 1.28.x sync tools ran
  directly on the event loop (func_metadata: `else: return fn(...)`), which is why a
  sync-tool `anyio.from_thread.run` elicitation bridge was impossible and async tools were
  the only route. In **2.0.0 that changed**: `func_metadata.py` now does
  `await anyio.to_thread.run_sync(functools.partial(fn, ...))` for the non-async branch, so
  sync tools DO run in a worker thread and such a bridge is now technically possible. The
  existing async msm/sandbox tools work fine and there is no reason to rewrite them — but
  the stated impossibility is no longer a valid argument.
