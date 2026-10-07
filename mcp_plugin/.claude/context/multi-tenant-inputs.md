# Multi-tenant mode and OAuth2: the inputs

The external facts the multi-tenant and OAuth2 plans rest on, as read on 2026-10-06:
the shell's secret groups, the customer's Snowflake reference, the MCP authorization
specification 2026-07-28, Keycloak's MCP support, the bundled Python, and the MCP
SDK's auth hooks. Moved out of [multi-tenant.md](multi-tenant.md) verbatim, so the
plan stays readable; nothing here changes with what was built.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Inputs

- **Shell secret groups**: one group per user, named by the user's UUID. See
  environment.md for the API and its limits. The limit that shapes the design:
  **groups partition, they do not protect**, so every tenant's isolation is
  enforced by the plugin, and the server should run under an OS account of its own.
- **Customer input on OAuth2:** Snowflake is the reference. The database is the
  OAuth authorization server, scopes mirror database roles, tools follow from the
  user's grants, there are no shared secrets, and connections are bound to the
  user. Details from Arcade's Snowflake page:
  - Clients request `session:role:all`.
  - The session runs as the user's `DEFAULT_ROLE`, capped by `ALLOWED_ROLES_LIST`.
  - There is no dynamic client registration.
  - Clients discover the authorization server through RFC 9728 Protected Resource
    Metadata.
  - "Access to the MCP server doesn't imply access to what it exposes."
- **MariaDB Server will NOT get an OAuth/JWT authentication plugin (user).** No
  token can log in to MariaDB, so the "pass the token through to the database"
  option is out. The MCP spec forbids passing tokens through anyway (below).
  Phase 2 considers **A (Keycloak)** and **C (built-in authorization server)**
  only **(user)**.
- **MCP authorization spec, revision 2026-07-28** (read 2026-10-06):
  - The server is an OAuth 2.1 resource server and **MUST** serve RFC 9728 PRM.
  - It **MUST** validate that a token was issued for it (audience, RFC 8707) and
    answer an invalid or expired token with 401.
  - It **MUST NOT accept or pass on any other token.**
  - It SHOULD put `scope` in the `WWW-Authenticate` challenge, and SHOULD answer a
    token with too few scopes with 403 `insufficient_scope` and the full set of
    scopes needed.
  - Client registration, in order of preference: **Client ID Metadata Documents
    (CIMD, SHOULD)**, pre-registration, then Dynamic Client Registration
    (**deprecated**, kept for compatibility).
  - Authorization servers SHOULD send `iss` in the authorization response
    (RFC 9207) and advertise `authorization_response_iss_parameter_supported`.
  - PKCE is required.
  - stdio "SHOULD NOT" use this flow, which fits stdio being refused in
    multi-tenant mode.
- **Keycloak** ([MCP guide](https://www.keycloak.org/securing-apps/mcp-authz-server),
  documented against nightly 26.8):
  - MCP 2025-03-26 is supported; later revisions are experimental.
  - RFC 8707 resource indicators are experimental (`--features=resource-indicators`).
    Without them, the audience comes from an **`Audience` mapper ("Included Custom
    Audience" = the MCP server URL) on Optional client scopes**.
  - Dynamic client registration is supported through anonymous-registration
    policies.
  - CIMD is experimental (`--features=cimd`), and ChatGPT's CIMD is NOT supported.
  - PKCE is required for public clients (VS Code, Claude Code, Claude Desktop).
  - Issuer `https://<host>/realms/<realm>`. Access tokens are RS256 JWTs with a
    5-minute lifespan by default.
- **Bundled Python** (checked 2026-10-06): PyJWT 2.15 (`PyJWKClient` for JWKS),
  cryptography 50, python-multipart, starlette and httpx are all present as MCP
  SDK dependencies. **No new dependency is needed**, but `requirements.txt` should
  name `pyjwt[crypto]` explicitly once the plugin imports it. The shell reports
  **mcp 2.2.0**, with 2.0.0, 2.1.1 and 2.2.0 `dist-info` directories side by side;
  environment.md still says 2.1.1, so re-check that before trusting either.
- **MCP SDK auth hooks**: read in the bundled tree on 2026-10-06, before the user
  moved the shell to the latest `mcp` release. **Re-check every point below
  against the new SDK** (phase 0):
  - `MCPServer(auth=AuthSettings(...), token_verifier=... | auth_server_provider=...)`.
    `streamable_http_app()`, which `_serve_streamable_http` already calls, then
    installs `BearerAuthBackend` + `AuthContextMiddleware`, wraps `/mcp` in
    `RequireAuthMiddleware` (401 with `WWW-Authenticate`, or 403 when
    `required_scopes` are missing), and serves PRM when `resource_server_url` is
    set.
  - `TokenVerifier.verify_token(token) -> AccessToken(client_id, scopes,
    expires_at, resource, subject, claims)`.
  - With an authenticated user, the **session-owner check** in
    `streamable_http_manager` becomes active. It is inert today (see
    connections.md) and binds an MCP session id to `(client_id, iss, sub)`.
  - The authorization-server side ships `/authorize`, `/token`, optional
    `/register` and `/revoke`, AS metadata, and the PKCE S256 check.
    `OAuthMetadata` has `client_id_metadata_document_supported` and
    `authorization_response_iss_parameter_supported` fields, but **`build_metadata`
    sets neither**, so they need our own metadata route. **Custom routes are added
    AFTER the SDK's**, so the override has to be inserted ahead of them.
  - `AuthSettings.issuer_url` is REQUIRED and must be https unless it is loopback.
  - `tools/list` is `MCPServer._handle_list_tools(ctx, params)`. Filtering it per
    caller means overriding that private method in a subclass, which needs a test
    pinning it against SDK upgrades.
