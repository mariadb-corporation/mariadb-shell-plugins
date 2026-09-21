/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
 * the GNU General Public License, version 2.0, for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 */

import preact from "@preact/preset-vite";
import { defineConfig } from "vite";

/**
 * Builds the connection editor's Preact frontend.
 *
 * It is a second, browser targeted build beside the extension's Node one:
 * the extension host and a webview are different runtimes and cannot share
 * a bundle. The output file names are fixed rather than hashed, because the
 * extension has to reference them from the webview's HTML.
 *
 * This is a build of its own rather than a second entry beside the result
 * panel, and the reason is not tidiness: Rollup would then
 * hoist what they share - Preact, `vscodeApi` - into a common chunk, which
 * each entry pulls in with a static `import`. A webview is served under
 * `script-src 'nonce-...'`, and a nonce does NOT carry to a module the
 * entry imports, so that chunk would be refused at load time and the view
 * would come up blank with only a console message to say why. One entry per
 * build has nothing to hoist.
 */
export default defineConfig({
    root: "webview",
    // Relative asset URLs. A webview is served from its own origin, so
    // an absolute `/codicon.ttf` would resolve to that origin's root
    // rather than beside the stylesheet, and the icon font would 404
    // without anything being reported.
    base: "./",
    plugins: [preact()],
    build: {
        outDir: "../dist/webview",
        // False, unlike the result panel's build: that one runs first and
        // clears the directory, and emptying it again here would take its
        // output away.
        emptyOutDir: false,
        sourcemap: true,
        target: "es2022",
        rollupOptions: {
            input: "webview/src/editor.tsx",
            output: {
                entryFileNames: "editor.js",
                // The stylesheet has a fixed name because the extension
                // references it from the webview's HTML. Everything else
                // - the codicon font, above all - keeps its own name,
                // since the stylesheet points at it by that.
                assetFileNames: (asset) => {
                    return asset.names?.[0]?.endsWith(".css") ?? false
                        ? "editor.css"
                        : "[name][extname]";
                },
                format: "es",
            },
        },
    },
});
