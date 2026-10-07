# OAuth2 built-in authorization server and Arcade (phase 2c/2d): implementation plan

Option C of phase 2: the MCP server as its own OAuth authorization server, logging
users in against MariaDB (2c), and the Snowflake-style setup through Arcade that
it must support (2d). Planned with the user and **built** on 2026-10-06 (see
Status).

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The phase 2 decisions it
refers to by number, the shared groundwork ("2a") and the Keycloak option ("2b")
are in [oauth.md](oauth.md). Phase 1 is in [multi-tenant.md](multi-tenant.md).

**Status: BUILT on 2026-10-06.** The as-built notes and deviations are in
[oauth.md](oauth.md) ("Status"). The manual end-to-end against a real Arcade project
(2d) is NOT done: it needs a public https deployment.

## 2c. Option C: the MCP server as its own authorization server, logging users in against MariaDB

This is the Snowflake picture without changing the server. **The database account
IS the identity**: a user signs in with their MariaDB username and password, and
the session runs as that account, under its default role.

- **Settings:**

  ```json
  "oauth": {"mode": "builtin",
            "loginServers": ["mariadb://db1.example.com:3306?ssl-mode=VERIFY_IDENTITY"],
            "requiredRole": "mcp_access",
            "autoProvision": {"enabled": true, "defaultScopes": ["mcp:db", "mcp:msm"]},
            "accessTokenLifetime": 3600,
            "grantMaxLifetime": 7776000, "grantIdleTimeout": null,
            "refreshGracePeriod": 30,
            "loginConnectionStore": "secret-store",
            "allowedClientNetworks": [],
            "dynamicClientRegistration": true, "cimd": true}
  ```

  - `requiredRole`, the counterpart of `GRANT USAGE ON MCP SERVER`: a login
    is accepted only when the account holds this MariaDB role (directly or
    nested, per `information_schema.APPLICABLE_ROLES`). It is checked on EVERY
    login, not only when a user is provisioned. `null` means any account that can
    log in.
  - `allowedClientNetworks`, the counterpart of Snowflake's network policies: an
    optional list of CIDRs that `/mcp`, `/token` and `/register` accept requests
    from. It is checked against the TCP peer, so it is only meaningful without a
    proxy, or with the proxy's own address listed. `/login` is not restricted,
    because the user's browser comes from anywhere.

  `mcp setup` options:
  - `--addLoginServer`, `--removeLoginServer`, `--oauthMode=builtin`,
    `--oauthRequiredRole`, `--oauthAutoProvision`,
    `--oauthDynamicClientRegistration`, `--oauthGrantMaxLifetime`,
    `--oauthGrantIdleTimeout`, `--oauthAllowedClientNetworks`
  - **client management** (see 2d for why each exists):
    - `--addOauthClient=<name>` with `--confidential`, and with `--redirectUris`
      optional at creation; it prints the client ID and, for a confidential
      client, the secret
    - `--setOauthClientRedirectUris=<client id>`
    - `--showOauthClientSecret=<client id>`, the counterpart of
      `SYSTEM$SHOW_OAUTH_CLIENT_SECRETS`
    - `--rotateOauthClientSecret`
    - `--setOauthClientAllowedRoles=<client id>`, the counterpart of
      `ALLOWED_ROLES_LIST`: a login through that client is accepted only when
      the role the session will run under (the user's `defaultRole`, or the
      account's default role) is in the list
    - `--removeOauthClient`, which ends every grant of that client
    - `--listOauthClients`, also in `--show`
  - `--rotateSigningKey`, `--revokeTokens`
- **SDK wiring:** `AuthSettings(issuer_url=<publicUrl origin>,
  resource_server_url=<publicUrl>, validate_token_resource=True,
  client_registration_options=<enabled per setting>,
  revocation_options=enabled)` with `auth_server_provider=BuiltinAuthProvider`.
  The SDK serves AS metadata, `/authorize`, `/token`, `/revoke` and `/register`,
  and our own routes add:
  - `GET/POST /login`: the sign-in and consent page, a plain HTML form with no
    JavaScript
  - **an AS-metadata route inserted AHEAD of the SDK's**, setting
    `client_id_metadata_document_supported` and
    `authorization_response_iss_parameter_supported`
- **Grants.** A grant is one user's authorization of one client, created by a
  successful login and consent. It holds the client, the scopes, the MariaDB
  account the user logged in with, the refresh-token family, and the times it was
  created and last refreshed.
  - **It ends** at the earliest of:
    - `grantMaxLifetime` (90 days by default, decision 7)
    - `grantIdleTimeout` without a refresh (off by default)
    - a revocation (`/revoke`, `mcp setup --revokeTokens`, an epoch bump)
    - refresh-token reuse
    - the user being disabled or removed
    - its client being removed
    - in memory mode, a server restart
  - Its access tokens carry the grant id and are refused once it has ended. The
    client then gets **401 with `WWW-Authenticate`** and logs in again.
- **The login connection (decision 3).** The login proves a MariaDB account and
  its password, and that becomes **a connection of the grant**, so `db.connect`
  works with no admin step.
  - **Where it is kept** (`loginConnectionStore`):
    - `"secret-store"` (default): in the user's group as
      `MCP:OAUTH:CONN:<grant id>`, a JSON value `{uri, password}`. It survives a
      server restart, as the grant does.
    - `"memory"`: held by the provider. A restart ends every grant.

    It is keyed by grant, NOT stored as `MCP:CONN:<uri>`, so it can never
    overwrite or delete a connection an admin stored for the same account, and
    two grants for one account (two clients) each own their copy.
  - **Deleted when the grant ends**, together with the grant record, and every
    open `_Connection` on it is dropped (as `_drop_connections_on` does for a
    deleted connection). This is what "as short as possible" means: the password
    lives exactly as long as something is authorized to use it.
  - **Ordinary in every other way.**
    - `db.list_connections` for a 2c principal shows the grant's login
      connection next to the connections an admin stored for the user.
    - `db.connect` resolves either kind and opens an ordinary `_Connection`.
    - **The reaper is unchanged:** idle sessions are closed and reopened
      transparently, and the 12 h hard TTL still drops the `_Connection` (the
      client calls `db.connect` again, which works while the grant lives).
    - `_open_session`'s re-validation checks, for a login connection, that **its
      grant is still alive** (the counterpart of "the URI is still configured")
      before reading the password.
    - The connection is visible only to principals of ITS grant, not to the
      user's other clients.
  - **Grant expiry has its own sweeper**, owned by the provider and started and
    stopped by `server.start()` like the reaper (which it does not touch). Expiry
    is also checked lazily on every token load and refresh. At startup in
    secret-store mode, the sweeper lists `MCP:OAUTH:*` across groups
    (`allGroups`) and deletes expired grants left by a server that stopped.
- **`BuiltinAuthProvider`:**
  - `get_client(client_id)`:
    - a pre-registered client (`oauth_clients.json`; a confidential client's
      secret goes in the generic group as `MCP:OAUTH:CLIENT:<id>`)
    - a client registered dynamically (also in `oauth_clients.json`; unused
      registrations expire after 30 days; `/register` is rate-limited)
    - **a CIMD client**, when `client_id` is an https URL
  - **CIMD fetch rules:**
    - https only, no redirects
    - SSRF guard: the resolved address must not be loopback, private or
      link-local, and the GET connects to those checked addresses, in the
      resolver's order, passing over one that does not connect (no second lookup,
      so no DNS rebinding; M17, 2026-10-07)
    - 5 s timeout, 5 KB limit
    - the document's `client_id` must equal the URL, and `redirect_uris` are
      matched exactly
    - cached between 5 minutes and 1 day, following Cache-Control; at most 1000
      documents, oldest dropped (M26)
  - `authorize(client, params)`: stashes the pending request (client, redirect
    URI, PKCE challenge, scopes, `resource`, state) in memory for 10 minutes (at
    most 10000, oldest dropped, and 30 `/authorize` per address per minute: M26) and
    returns `/login?req=<id>`. **It is lenient where it can safely be:**
    - A `resource` other than `publicUrl` is refused, but a MISSING one is taken
      as `publicUrl`. The spec requires clients to send it, but not every client
      does yet.
    - Requested scopes this server does not know are dropped, not refused
      (clients send whatever their UI was configured with). If NO known scope is
      requested, the user's own `scopes` are offered.
  - **The `/login` POST:**
    1. CSRF token bound to the pending request.
    2. Rate limits per peer and per `(server, username)`, with exponential
       backoff and a generic failure message. (As built: a fixed 15-minute
       window, 5 failures per account and 30 per address, counted by the same
       `auth.FailureCounter` the bearer-token throttle uses, since `176aee35`.) **The form is a password oracle
       against the database, so this is not optional.** Per-peer limits are
       right HERE because `/login` is the user's own browser. On `/token` and
       `/mcp` they are not (see 2d).
    3. Open a session on the chosen login server **as the user**. TLS is required
       unless the server is loopback. Then `SELECT CURRENT_USER()`, and close the
       session again.
    4. Map `{"type": "mariadb", "server", "account"}` to a user:
       - an existing identity;
       - otherwise auto-provision a new user with that identity (decision 2);
       - **several MariaDB accounts on one user (decision 4)** come from an admin
         linking them (`--addIdentity --user=<id> --mariadbAccount=ada@% --server=…`,
         or `--mergeUsers` for two auto-provisioned users). A login never links
         by itself, because a MariaDB account carries no verified email to link
         by.
    5. **Role checks**, all from `information_schema.APPLICABLE_ROLES`, each
       refusing the login with a clear message:
       - the account holds `requiredRole`, when one is set; this runs BEFORE
         step 4, so an account without it is never provisioned
       - the user's `defaultRole`, if any, is granted to the account (it is
         applied on every session open, 2a)
       - the effective role is in the client's `allowedRoles`, when that is set
    6. Consent: the client's name and redirect host, plus the requested scopes
       intersected with the user's `scopes` (each can be unticked).
    7. Create the grant and its login connection, issue the code, and redirect
       with `code`, `state` **and `iss`**. The password reaches no other place
       than the login connection.
  - **Codes:** in memory, single-use, 60 s. They are bound to the client, the
    redirect URI, the PKCE challenge (the SDK checks S256), the resource and the
    grant. A code not redeemed in time ends its grant, deleting the login
    connection.
  - **Access tokens:** JWTs **signed by the server**, ES256. The key is generated
    on first start and kept in the generic group as `MCP:OAUTH:SIGNING_KEY`, with a
    `kid`.
    - Claims: `iss`, `aud=publicUrl`, `sub=<mcp_user_id>`, `client_id`, `scope`,
      `iat`, `exp` (1 h, capped at the grant's end), `jti`, `epoch`, `grant`.
    - `load_access_token` checks the signature, `aud`, `exp`, `epoch` against
      `tokenEpoch`, and **that the grant is still alive**.
    - `scope` is the grant's consented scopes narrowed to the user's CURRENT
      `scopes`, both when signed (every refresh) and when checked (every request),
      so `--setScopes` applies at once; a grant left with none lives on with
      scopeless tokens until a scope is restored (M24, 2026-10-07).
  - **Refresh tokens:** opaque `mdbrt_<uuid>_<random>` (the UUID picks the group
    to read). Only a hash is kept, in the grant record: `MCP:OAUTH:GRANT:<grant
    id>` in the user's group, or in memory under `"memory"`. They **rotate on
    every use**, each refresh resets the idle timeout, and reusing a spent token
    ends the whole grant (OAuth 2.1).
    - **Grace period** (`refreshGracePeriod`, 30 s): a server-side client may
      send the same refresh token twice in a race (two workers refreshing one
      user). Within the grace period the just-replaced token gets the SAME new
      pair back rather than counting as reuse. After it, reuse ends the grant as
      above.
  - `revoke_token` handles both kinds and ends the grant.
- **Security checklist:**
  - redirect URIs matched exactly; loopback redirects may vary the port, per
    OAuth 2.1
  - `X-Frame-Options: DENY`
  - CSP `default-src 'none'; style-src 'self'; form-action 'self'`
  - `Cache-Control: no-store` on `/login` and `/token`
  - the password is never logged and is stored only as the grant's login
    connection, and an audit event for every login (with outcome), consent,
    token issue, refresh reuse, grant end and revocation
  - DNS-rebinding validation still covers `/mcp`, while `/login` relies on CSRF
    and `form-action`
  - the signing key is not exportable through any tool
- **Tests:**
  - in-process full flow (Starlette `TestClient` plus the SDK's OAuth client)
    against the shared sandbox: real logins as sandbox users; the default role
    applied, with a revert probe
  - **The password lives exactly as long as the grant**, in both store modes. A
    sentinel password is found only in `MCP:OAUTH:CONN:<grant>` while the grant
    lives, and is gone from the store and from memory after each way a grant
    ends:
    - idle timeout
    - max lifetime
    - revoke
    - epoch bump
    - refresh reuse
    - user disabled or removed
    - an expired grant swept at startup

    Each of those is followed by a 401.
  - the login connection is idle-closed and transparently reopened by the
    unchanged reaper; a reopen after the grant ended is refused
  - an admin connection to the same account is untouched by a grant ending
  - the login connection is not visible to the same user's other grant
  - several accounts on one user, an admin link, a merge
  - PKCE failure, code reuse, an unredeemed code ending its grant, refresh
    rotation and reuse detection
  - CIMD: a good document, a mismatched `client_id`, SSRF to a private address
    (all with a mocked fetch); dynamic registration on and off
  - rate limiting, CSRF, the `iss` parameter, the metadata flags
  - `requiredRole`: an account without it is refused and not provisioned; an
    existing user who loses the role is refused at the next login
  - a client's `allowedRoles`; a missing `resource`; unknown scopes; no scope
  - the refresh grace period: a duplicate within it gets the same pair, and
    after it ends the grant
  - the 90-day default, and the idle timeout off unless configured
  - `allowedClientNetworks` on `/mcp` and `/token`, not on `/login`
  - removing a client ends its grants

## 2d. Arcade (and other server-side MCP gateways)

Target (decision 8): the same setup as
[Arcade's Snowflake guide](https://docs.arcade.dev/en/operate/governance/remote-mcp-servers/snowflake),
with 2c as the authorization server. Arcade is a **confidential, pre-registered
client** that runs the OAuth flow **per end user** and stores each user's tokens
separately. It discovers the endpoints through PRM, and **calls `/token` and
`/mcp` from its own servers**.

**How the Snowflake steps map:**

| Arcade + Snowflake | Arcade + this plugin (2c) |
| --- | --- |
| `CREATE SECURITY INTEGRATION … OAUTH_CLIENT_TYPE='CONFIDENTIAL'` with a placeholder redirect URI | `mcp setup --addOauthClient=arcade --confidential`, which prints the client ID and secret |
| `SYSTEM$SHOW_OAUTH_CLIENT_SECRETS(...)` | `mcp setup --showOauthClientSecret=<id>` |
| Register the server in Arcade: the URL, the client ID and secret, the authorization and token URLs left EMPTY | the same, with `publicUrl` as the URL; Arcade discovers the endpoints from PRM and the AS metadata |
| `ALTER SECURITY INTEGRATION … SET OAUTH_REDIRECT_URI = '<arcade redirect>'` | `mcp setup --setOauthClientRedirectUris=<id> --redirectUris=<arcade redirect>` |
| `ALLOWED_ROLES_LIST = ('mcp_access_role')` | `--setOauthClientAllowedRoles=<id> --roles=mcp_access` |
| `GRANT USAGE ON MCP SERVER … TO ROLE mcp_access_role` | `oauth.requiredRole = "mcp_access"`, plus `GRANT mcp_access TO …` in MariaDB |
| `ALTER USER … SET DEFAULT_ROLE = 'mcp_access_role'` | `SET DEFAULT ROLE mcp_access FOR …` in MariaDB, or a `defaultRole` per user |
| Object grants decide what the tools can reach | the same: MariaDB grants to the account and its role |
| Network policy allowing Arcade's egress IPs | `oauth.allowedClientNetworks` (optional) |
| Refresh tokens valid 90 days by default | `grantMaxLifetime` = 90 days by default |

**What a server-side gateway changes, and how 2c handles it:**

- **Every user arrives from Arcade's few egress addresses.**
  - **Rate limits on `/token` and `/mcp` are not keyed on the peer alone.**
    Failures count per `(peer, client_id)` on `/token` and per `(peer, user)` for
    a bad bearer token, with only a high per-peer ceiling, so one user's bad
    tokens cannot lock out every Arcade user. This applies to phase 1's API-key
    limit as well.
  - The connection binding still separates users, through the user and MCP
    session id halves of `ClientIdentity`.
  - `MAX_CONNECTIONS_TOTAL` must be raised for many users (a `startServer`
    option, phase 1). The per-user cap is what binds per user.
- **Arcade discovers tools once, under the admin's own login**, so that
  admin's account and consent must cover `mcp:db` and `mcp:msm`. Otherwise the
  msm tools stay undiscovered for everyone. Say so in the README.
- **Host and Origin:** `publicUrl`'s host is allowed automatically (2a). Arcade's
  server-side requests carry no `Origin`, which the transport security accepts.
- **TLS is mandatory in practice**, since Arcade needs a public https URL: the
  TLS options from phase 1, or a TLS-terminating proxy.

**Deliberate differences from Snowflake:**

- **The DB password is stored** for the grant's life (decision 3). MariaDB cannot
  validate our tokens itself. Arcade never sees the password; it holds only our
  tokens.
- **No per-request role switching**: the session runs under the default role.
  This matches what Arcade's Snowflake guide relies on (`DEFAULT_ROLE`), since
  `session:role:all` gives it no runtime choice either.

**Tests and verification:**

- **An automated Arcade-shaped test:** a confidential client registered via setup
  with the redirect URI set afterwards, discovery from PRM only, the
  `client_secret_post` and `client_secret_basic` token calls, two users through
  one client from ONE peer address, one user's failures not throttling the
  other, a duplicate refresh within the grace period, and an account without
  `requiredRole` refused.
- **A manual end-to-end** against a real Arcade project before 2c is called
  done, recorded here with the Arcade version and any deviation.
  - It must confirm three things: whether Arcade sends `resource`, which token
    endpoint authentication method it uses, and which scopes it requests. 2c
    tolerates every answer, but the README should state what was observed.
  - It needs a publicly reachable https test deployment.
  - **DONE 2026-10-06** with the user's Arcade project, through a `cloudflared` quick
    tunnel (`https://….trycloudflare.com`), a throwaway `mariadbd` (account `ada`, role
    `mcp_access`, read-only `demo`), and Arcade's `/v1/tools/execute` with the user's
    API key. Observed:
    - Arcade sends `resource` (the public URL), PKCE S256, `scope=mcp:db mcp:msm`, and
      uses the pre-registered confidential client; its token call succeeded (method not
      recorded). The redirect URI is per server:
      `https://cloud.arcade.dev/api/v1/oauth/<id>/callback`, shown only once the server is
      added, so the first authorize fails until `--setClientRedirectUris` is run.
    - **Arcade refuses dotted tool names** ("only ASCII letters, numbers, dash and
      underscore"; it validates `Mariadb.db.list_connections@0.0.0`). Fixed by
      `mcp setup --toolNameSeparator=_`; tools are then called as
      `mariadb.db_list_connections` (`<server id>.<tool>`). 20 tools (db + msm).
    - **Arcade opens a new MCP session for every tool call** (initialize, call, DELETE),
      so session-bound connections broke after `db_connect` (M23). Fixed by binding to
      user + grant.
    - **Arcade's user verification** (on by default): a flow for a `user_id` that is not
      the signed-in Arcade account fails after our redirect, with no `/token` call. Use the
      account email (`ARCADE_USER_ID` in the user's env file). The dashboard's admin
      sign-in already authorized that user, so tools ran without a second sign-in.
    - Result: connect, list schemas, `SELECT … CURRENT_USER(), CURRENT_ROLE()` →
      `ada@%`/`mcp_access`, `DELETE` refused by MariaDB (1142), close.
    - The README and the docs-ref Arcade section record all of this.
