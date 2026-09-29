# Toolchain

How `code_ext` is built, tested and shipped: the Vite builds, the two
Vitest projects, the pinned Node, the Marketplace icon and CI.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

- **Build**: [Vite](https://vite.dev), twice. `vite.config.ts` bundles
  `src/extension.ts` into a single CommonJS `dist/extension.js` for the
  extension host, with `vscode` and the Node built-ins external.
  `vite.webview.config.ts` builds the Preact frontend into
  `dist/webview/main.js` and `main.css` for the browser. The two dialogs
  are a build each as well - `vite.editor.config.ts` (the connection
  editor, `editor.js`/`editor.css`) and `vite.sandbox.config.ts` (New
  Sandbox, `sandbox.js`/`sandbox.css`) - because one build with several
  entries hoists a shared chunk that a webview's script nonce refuses (see
  [connection-editor.md](connection-editor.md)). `build:webview` runs all three. The extension
  build has `emptyOutDir: false` on purpose: it runs again on every change
  in watch mode, and emptying `dist/` would delete the panel's assets out
  from under a running extension host. `npm run clean` wipes `dist/`, and
  `npm run build` runs it first.
- **Tests**: [Vitest](https://vitest.dev), in two projects, because the
  two halves run in different places. The **extension** project runs
  `src/test/` in plain Node with the `vscode` module aliased onto
  `src/test/mocks/vscode.ts`, so no extension host is needed. The
  **webview** project runs `webview/test/` under jsdom with the Preact
  plugin. Either can be run alone with `--project extension` or
  `--project webview`.
- **Type checking**: `tsc --noEmit` against two configs — `tsconfig.json`
  for the extension (Node) and `tsconfig.webview.json` for the frontend
  (DOM + Preact JSX).
- **Lint**: ESLint with `typescript-eslint`, over `src` and `webview`.
- **Webview assets are files, never inlined**: `vite.webview.config.ts`
  has `assetsInlineLimit: 0`, and `assetFileNames` puts an icon from
  `images/light/` or `images/dark/` under `dist/webview/icons/light|dark/`
  (the two variants share a name; without the folder Rollup would append
  a number to one). The stylesheet references them relatively, as it does
  the codicon font; `src/test/webview/assets.test.ts` pins both rules.
  Most toolbar and data icons are drawn as CSS masks from the LIGHT file.

| Script | What it does |
| --- | --- |
| `npm run build` | Clean `dist/`, then bundle the extension and the webview. |
| `npm run clean` | Remove `dist/`. |
| `npm run watch` | Rebuild the extension on change. |
| `npm run watch:webview` | Rebuild the webview on change. |
| `npm test` | Run both Vitest projects once. |
| `npm run coverage` | Run the suite with a V8 coverage report. |
| `npm run typecheck` | Type check both configs. |
| `npm run lint` | Lint `src` and `webview`. |
| `npm run pretest` | `typecheck` + `lint`. |

## Node version, and why the lockfile keeps churning

**Node is pinned exactly, in `.nvmrc` (currently `24.21.0`)**, with
`engines.node` in `package.json` as the documented floor (`>=24.15.0`). Both
numbers are load bearing:

- The jsdom stack the webview tests run on (`jsdom`, `@asamuzakjp/dom-selector`,
  `@asamuzakjp/css-color`) requires `^22.22.2 || ^24.15.0 || >=26.0.0`. Node 22.19
  produces three `EBADENGINE` warnings and runs the suite on an unsupported
  runtime; Node 24.21 produces none.
- Node 24 ships **npm 11**, and npm 11 writes the `libc` fields that
  `package-lock.json` carries for the optional rollup binaries. **npm 10 silently
  strips them**, so a contributor on Node 22 rewrites the lockfile just by
  installing. That is the churn — it is not noise to be ignored, it is two npm
  majors disagreeing about the file's format.

`package-lock.json` **is** checked in, and should be: this is an application
(shipped as a `.vsix`, with the four runtime deps bundled into
`dist/extension.js` by Vite), not a library whose consumers resolve their own
tree. What the lockfile pins is literally what ships — which matters most for
`@modelcontextprotocol/sdk`, since `src/mcp/protocol.ts` decodes shapes read off
the running server and a floating minor could change them underneath a release
build. `package.json` also carries `"private": true`, since the extension is
never published to npm.

**Always `npm ci`, never `npm install`, in CI.** `npm ci` installs exactly the
locked tree and never writes the lockfile back.

## The Marketplace icon

`images/marketplace-icon.png`, declared by `icon` in `package.json`. It is a
**256x256** PNG: the manifest reference asks for "at least 128x128 pixels
(256x256 for Retina screens)". Two traps:

- **`icon` may not be an SVG.** vsce rejects it outright, and that is the only
  icon rule vsce enforces - it checks no dimensions at all, so an undersized
  PNG packages happily and is only rejected at the Marketplace end. The SVG ban
  applies to this field alone; the `contributes` icons (`mariadb-seal.svg`, the
  `light/` and `dark/` trees) stay vectors.
- **There is no light/dark variant.** The one image sits on both Marketplace
  card backgrounds, so the artwork is a tile with an opaque body and alpha only
  in the rounded corners - a transparent logo would vanish on one of them.

vsce publishes it as the `Microsoft.VisualStudio.Services.Icons.Default` asset.
The source artwork lives outside the repo, under
`Work/Artwork/Logos/MariaDB VS Code Extension/`; copying from there carries the
volume's mode and xattrs, so `chmod 644` and `xattr -c` afterwards or git
records the file executable.

## CI

`.github/workflows/shell-plugins-ci.yml` has a `Code-Ext-CI-Verification` job
running `npm ci` -> `npm run pretest` -> `npm test` -> `npm run build`, on the
Node that `code_ext/.nvmrc` names. The build step is there because the extension
ships as a bundle: an import that only resolves under the test aliases, or an
asset that is not copied, is invisible to the type check and the tests alike.

Which suites run is decided once, by a `changes` job whose outputs the plugin
job and this one both read — `code_ext/**` gates this job, and each `*_plugin/`
prefix gates its own step. Adding a fifth thing to test means adding it to the
loop in that job **and** reading the new output somewhere.
