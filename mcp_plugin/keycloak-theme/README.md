# Keycloak Login Theme for the MariaDB MCP Server

`mariadb-mcp` is a Keycloak login theme that gives the sign-in pages of a realm the look of the MCP server's own sign-in page: the MariaDB logo, the "MariaDB MCP Server" header, the joined user name and password fields with the round "next" button, and the light and dark color schemes. Users who sign in to a [Keycloak-mode](../README.md#keycloak) MCP server then see the same page as with the built-in authorization server.

The theme changes only how the pages look. It works with any realm, including one prepared with `mcp setup-keycloak-realm`, and with identity providers such as Google or Facebook, whose buttons appear below the fields.

## Contents

| File | Purpose |
| --- | --- |
| `mariadb-mcp/login/theme.properties` | Declares the theme. It extends Keycloak's `base` theme. |
| `mariadb-mcp/login/template.ftl` | The page frame of every login page: logo, header, card, footer. It keeps all scripts of the base template. |
| `mariadb-mcp/login/login.ftl` | The sign-in page. |
| `mariadb-mcp/login/logo.ftl` | The official MariaDB logo, inline so that the stylesheet can color it: the logo's blue in the light scheme, white in the dark one. |
| `mariadb-mcp/login/resources/css/sign-in-page.css` | The stylesheet of the MCP server's own sign-in page, unchanged. |
| `mariadb-mcp/login/resources/css/mariadb-mcp.css` | The styles for Keycloak's other login pages, on top of it. |
| `mariadb-mcp/login/messages/messages_en.properties` | The header, welcome, and footer texts. |

All other pages, such as updating a profile after the first sign-in through an identity provider, come from the `base` theme and use the same frame and styles.

The theme was built for Keycloak 26. Its `template.ftl` is a copy of the base theme's template with a different layout, so check it against the base template of a new major Keycloak version.

## Install

The theme ships with MariaDB Shell, in `lib/mariadb-shell/plugins/mcp_plugin/keycloak-theme/mariadb-mcp` below the installation directory, and in the MariaDB Shell container image under `/opt/mariadb-shell`.

Keycloak loads themes from its `themes` directory at startup. Copy the `mariadb-mcp` folder there, or mount it into the container.

With the Keycloak container image, mount the folder read-only:

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.8.0
    volumes:
      - ./keycloak-theme/mariadb-mcp:/opt/keycloak/themes/mariadb-mcp:ro
```

For a Keycloak installed from the distribution, copy the folder to `<keycloak>/themes/mariadb-mcp`.

Restart Keycloak, then select the theme for the realm:

* With `mcp setup-keycloak-realm`, which checks that Keycloak has the theme before it changes anything in the realm:

  ```bash
  mariadb-shell -- mcp setup-keycloak-realm --server=https://kc.example.com --realm=mariadb \
    --adminUser=admin --loginTheme=mariadb-mcp
  ```

  The MariaDB Shell container image passes `KEYCLOAK_LOGIN_THEME` to this option on its first start.
* In the admin console, open the realm, go to **Realm settings** → **Themes**, set **Login theme** to `mariadb-mcp`, and save.
* Or with the admin CLI:

  ```bash
  kcadm.sh update realms/<realm> -s loginTheme=mariadb-mcp
  ```

To see the result, open `https://<keycloak>/realms/<realm>/account/` in a private browser window. A browser that is still signed in to the realm skips the sign-in page.

## Customize

* **Texts:** change `messages/messages_en.properties`, or add `messages_<language>.properties` files for other languages. The base theme provides all other texts in every language it supports.
* **Colors:** the variables at the top of `sign-in-page.css`, `:root` for the light and the `prefers-color-scheme: dark` block for the dark scheme.

After a change, restart Keycloak. Keycloak keeps a compressed copy of each stylesheet under `data/tmp/kc-gzip-cache` and serves it to browsers that accept gzip, which is every browser. A restart doesn't clear that directory, so after changing anything under `resources`, also delete the theme's copies:

```bash
rm -rf <keycloak>/data/tmp/kc-gzip-cache/*/login/mariadb-mcp
```

Browsers also cache the stylesheet. Check a change in a private window.

## Keeping It in Line with the MCP Server's Page

`sign-in-page.css` and the logo in `logo.ftl` are copies of `lib/assets/sign-in-page.css` and `lib/assets/mariadb-logo-vertical.svg`, which the MCP server's own sign-in page uses. `tests/unit/test_keycloak_theme.py` fails when they drift apart. After changing the server's page, copy the files over. Changing a color in the theme's copy alone makes the test fail; change it in both, or override it in `mariadb-mcp.css`.
