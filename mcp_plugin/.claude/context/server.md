# The server: building it, serving it, securing it

`mcp.startServer` end to end — GUI mode, the function groups, the two
transports, the uvicorn server the plugin runs itself, the Host/Origin
validation, and the path guard the msm and sandbox tools elicit through.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The connection and
session machinery the db tools sit on is in
[connections.md](connections.md).

## Architecture / key decisions

- **GUI mode (`mcp start-server --gui`)** — the server is being driven by the MariaDB
  VS Code extension (`code_ext`) rather than by an autonomous agent. Set by
  `lib/server.start()` via `general.set_gui_mode()` **before the tools are built**,
  because it decides which tools exist; read by exactly two places:
  - `config.is_path_allowed()` returns True for everything. This is the ONE chokepoint —
    both `db.execute_sql_script`'s own check and `general.require_allowed_path()` go
    through it, so there is no second place to keep in step. The allowed-path list on
    disk is left untouched, so turning the mode off restores the old answers exactly.
  - `db_functions.register_db_tools()` registers `_register_connection_management_tools`
    instead of the plain `db.list_connections`, adding `db.add_connection` and
    `db.delete_connection`.

  It is deliberately NOT something a client can ask for over the protocol: it is decided
  on the command line that started the server. Over HTTP it is allowed but WARNED about
  (`_warn_if_gui_mode_over_http`) — there is no authentication, so it would hand full
  file access to whoever reaches the port.

- Server runs **foreground on the main thread**; `start()` sets `useWizards=False`.

- **streamable-http is served on OUR OWN uvicorn server** (`_serve_streamable_http`), NOT
  via `mcp_server.run(transport=...)`. Verified on the bundled SDK: 2.0.0's
  `run_streamable_http_async` builds `uvicorn.Config(app, host=, port=, log_level=)`
  internally and forwards NOTHING else, so `proxy_headers` cannot be reached through
  `run()`. `_serve_streamable_http` does exactly what the SDK does (same
  `streamable_http_app`, same `settings.log_level`) plus TWO deliberate differences, both
  security fixes and neither a style choice — see Gotchas:
  - **`proxy_headers=False`** on the `uvicorn.Config` (S1).
  - **an explicit `transport_security=`** built by `_transport_security_settings` (S4),
    instead of letting `streamable_http_app` decide.

- **DNS-rebinding / Host+Origin validation is configured HERE, never left to the SDK**
  (`_transport_security_settings` + `_dialable_host_names`). Verified on the bundled SDK:
  `TransportSecuritySettings.enable_dns_rebinding_protection` defaults to **True**, but
  `TransportSecurityMiddleware.__init__` does
  `settings or TransportSecuritySettings(enable_dns_rebinding_protection=False)` — so
  passing None means OFF. 2.0 did NOT drop the 1.x auto-enable, it moved it into
  `lowlevel/server.py streamable_http_app()`:
  `if transport_security is None and host in ("127.0.0.1", "localhost", "::1")`. `host` DOES
  reach it, so the plugin's DEFAULT bind was already protected before this change — the
  defect is the CONDITION: a case-sensitive test over three literal strings, so `LOCALHOST`,
  `[::1]`, `127.0.0.2` (still loopback!) and EVERY non-loopback bind served with no
  validation at all. Now always enabled, allow list derived from the real bind host:
  loopback bind -> all of `general.LOOPBACK_HOST_NAMES`; a single address/name -> only that
  one (loopback deliberately NOT added, it is genuinely unreachable then), bare IPv6
  bracketed; wildcard (`general.is_wildcard_host`) -> loopback + `socket.gethostname()` /
  `getfqdn()` / its resolved addresses. Every name is allowed BARE and with `:{port}` — the
  SDK's own list had only `host:*`, which 421s a Host without a port (i.e. `--port=80`).
  Origins mirror the hosts over http+https with `:*`.

- **`allowed_hosts` option on `mcp.startServer`** (list or comma-separated string): extra
  Host values, for a server reached under a name that cannot be derived from the bind
  address — a reverse proxy, a port forward, a DNS alias. NEEDED, not decoration: without
  it, enabling the validation on a wildcard bind would 421 legitimate remote clients, which
  is worse than the status quo it replaced.

- **There is NO authentication.** Anyone who can reach the port can `db.list_connections` +
  `db.connect` and get a session on the stored credentials; the connection binding stops
  takeover, not unauthorized use. Stated outright in the README (its own section, placed
  BEFORE the connection-handling one), and `_warn_if_reachable_from_the_network` prints a
  stderr warning naming that risk when `--host` is not loopback
  (`general.is_loopback_host`).

- Transports: `streamable-http` (default) and `stdio`. **stdio hardened** (`_serve_stdio`):
  real stdout (fd 1) dup'd for the transport, then fd 1 AND `sys.stdout` redirected to
  stderr so tool/shell/C output can't corrupt JSON-RPC. Uses low-level
  `mcp_server._lowlevel_server.run(...)`.

- **Function groups** (`function_groups`): `db`, `msm`, `sandbox`; `_FUNCTION_GROUP_REGISTRARS`.
  Accepted as a LIST or as a comma-separated STRING (split+stripped in `server.py`);
  omitting it loads ALL THREE (`DEFAULT_FUNCTION_GROUPS = SUPPORTED_FUNCTION_GROUPS`).

- **Cross-group dependency mechanism**: `build_mcp_server` passes the full enabled-group
  list to EVERY registrar, so `register_db_tools`/`register_msm_tools`/
  `register_sandbox_tools` all take `(server, function_groups=())`. db and sandbox ignore
  it. msm uses it to register `msm.deploy_schema` ONLY when `db` is also served — that tool
  needs a `connection_id` from `db.connect`, so with msm alone it is left UNADVERTISED
  rather than exposed as something that cannot succeed. It is therefore registered LAST in
  the registrar, inside an `if` (see Gotchas — an early `return` there silently drops the
  tools defined after it).

- **Allowed paths**: `settings.json` under `general.get_plugin_data_path()`.
  `config.is_path_allowed()` via `os.path.commonpath` (reads disk fresh each call — NO
  in-memory cache); empty list => deny all.

- **Path enforcement + elicitation** (commit 6e12ad68): shared guard
  `general.require_allowed_path(ctx, path)` (ASYNC; skips when arg is None), used by msm.*
  (target/file/schema_project) and sandbox.* (sandbox_dir). When a path is NOT allowed it
  MCP-elicits (`ctx.elicit`, schema=one-bool `ConfirmTrustPath`) asking the user to trust
  it; on accept+trust it `config.add_allowed_path()` (persists to settings.json,
  abspath+expanduser, dedup) and proceeds; on decline/cancel/elicit-failure it raises the
  "not allowed" mysqlsh.Error. Because elicit is async, ALL msm (12) + sandbox (7) tools are
  `async def` with a leading `ctx: Context` param (`from mcp.server.mcpserver import Context`,
  imported inside the registrar; the server strips it from the client-facing schema).
  db.* tools stay SYNC — none of them elicit (`db.execute_sql_script` checks
  `config.is_path_allowed` directly and just errors out) — but they DO take `ctx` now, see
  the connection-safeguards bullet above.

## Files that matter

- lib/server.py -> build/serve; `_FUNCTION_GROUP_REGISTRARS` (a (module, function) NAME
  pair per group) + `_registrar()`, which resolves it with `importlib` when the group is
  served — the laziness that lets `migrator_functions` import the SDK at module scope;
  `_serve_stdio` hardening; `_serve_streamable_http`
  (own uvicorn, `proxy_headers=False`, explicit `transport_security`);
  `_transport_security_settings` + `_dialable_host_names` (the Host/Origin allow list);
  `_warn_if_reachable_from_the_network`; passes function_groups to the registrars.

- lib/tool_registrar.py -> the `server.tool` replacement db/msm/sandbox register through,
  converting a `mysqlsh.Error` into a `ToolError` so SDK 2.1 does not strip its message
  (see the SDK-error gotcha — this module was deleted once and had to come back).
  `ToolError`, `ResourceError` and `MCPError` pass through unconverted. Imports the SDK
  inside `decorator`, never at module scope. **100% covered.**

- tests/unit/test_tool_registrar.py -> the 6 wrapper tests. `_FakeServer.tool` returns the
  wrapper instead of registering it, so these call it DIRECTLY: no server, no protocol,
  0.6s. They cover the passthrough types no tool group raises today — which is the half
  the stdio round trips cannot reach.

- tests/unit/test_server_binding.py -> the bind address: loopback/wildcard classification,
  the no-authentication warning, and the derived Host/Origin allow lists. Pure in-process,
  no server started, so it is fast and needs no sandbox.

- tests/unit/test_gui_mode.py -> the 28 GUI-mode tests: the flag, the path bypass (both
  that it is on AND that the allow-list is not written to), which tools are served,
  the two lists through the tools, `db.connect` preferring the GUI entry, per-list
  revocation, and the HTTP warning. `_empty_both_connection_lists()` is needed because
  `clean_config` restores but does NOT clear, so a developer's own connections would
  otherwise show up in the exact-list assertions. One test starts a REAL shell
  (`helpers.list_tool_names(["db"], gui=True)`) and is the only thing that pins the
  actual `--gui` command-line spelling; everything else drives the plugin in-process.

## Gotchas / things not to repeat

- **NEVER serve streamable-http with `mcp_server.run(transport=...)` again.** It re-opens
  S1: the SDK builds its own `uvicorn.Config` with uvicorn's `proxy_headers=True` default,
  and `ProxyHeadersMiddleware` then rewrites the peer address from `X-Forwarded-For` for
  every request from a trusted peer — which, on the default `127.0.0.1` bind, is every
  request. `_serve_streamable_http` exists solely to pass `proxy_headers=False`. Equally:
  do NOT "simplify" it to the `FORWARDED_ALLOW_IPS` env var — what an empty trust list
  means is uvicorn-version-dependent (in 0.52.1 it happens to become `trusted_literals =
  {""}`, matching nothing, but that is incidental), whereas `proxy_headers=False` keeps the
  middleware from being installed at all.

- **NEVER let the SDK decide the DNS-rebinding protection.** That was S4: pass
  `transport_security=` explicitly. `streamable_http_app` enables it only for the exact,
  case-sensitive strings `"127.0.0.1"`, `"localhost"`, `"::1"`, so `LOCALHOST`, `[::1]`,
  `127.0.0.2` and every non-loopback bind serve with NO Host or Origin validation. Do not
  "simplify" `_transport_security_settings` away because the default bind happens to be
  covered by the SDK — the whole point is that the coverage is a string match.
  `test_streamable_http_rejects_a_foreign_host_header` binds `--host=LOCALHOST` precisely to
  sit outside that match; do not "fix" it to 127.0.0.1, it would then pass on the SDK's
  behaviour and stop testing ours.

- **Never guard a tool registration with an early `return` in a registrar** — the tools
  defined AFTER it silently stop being registered. `msm.deploy_schema`'s gate first used
  `if db not in function_groups: return` placed mid-registrar, which would have dropped
  `msm.get_deployment_script_versions` from every msm-only server. Register conditional
  tools LAST, inside an `if`, and assert the neighbouring tools still exist in both
  configurations (`test_stdio_deploy_schema_requires_the_db_group` does).

- **Elicitation is async**: `await ctx.elicit(message, schema=BaseModel)` ->
  ElicitationResult with `.action` ("accept"/"decline"/"cancel") and `.data`. Tools must be
  `async def` and declare `ctx: Context`.

- **No-hang for elicit**: a ClientSession without an elicitation_callback does NOT advertise
  the capability; server's elicit sends anyway and the client's default callback returns
  `ErrorData("Elicitation not supported")` -> server raises McpError -> guard `except` ->
  returns False -> "not allowed" error. In tests, pass `elicitation_callback` to answer
  (`types.ElicitResult(action="accept", content={"trust": True})`).

- **stdio needs clean stdout** — don't add prints to the stdio path.

- Don't reintroduce bg-thread+SIGTERM serving nor the interactive guard in `start()`.
  Plain GPLv2+MariaDB header. tests/ has no `__init__` (namespace pkg + `PYTHONPATH=..`).
