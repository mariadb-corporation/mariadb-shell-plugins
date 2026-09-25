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
 * Builds the result panel's Preact frontend.
 *
 * It is a second, browser targeted build beside the extension's Node one:
 * the extension host and a webview are different runtimes and cannot share
 * a bundle. The output file names are fixed rather than hashed, because the
 * extension has to reference them from the webview's HTML.
 *
 * The connection editor is a THIRD build, in `vite.editor.config.ts`, and
 * the two are deliberately not one build with two entries: Rollup would then
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
        emptyOutDir: true,
        sourcemap: true,
        target: "es2022",
        // The icons are served as files, in a light and a dark variant,
        // rather than folded into the stylesheet as data URIs.
        assetsInlineLimit: 0,
        rollupOptions: {
            input: "webview/src/main.tsx",
            output: {
                entryFileNames: "main.js",
                // The stylesheet has a fixed name because the extension
                // references it from the webview's HTML. Everything else
                // - the codicon font, above all - keeps its own name,
                // since the stylesheet points at it by that.
                //
                // An icon keeps its theme folder: the light and dark
                // variants share a name, and would otherwise be told
                // apart by a number Rollup appends.
                assetFileNames: (asset) => {
                    if (asset.names?.[0]?.endsWith(".css") ?? false) {
                        return "main.css";
                    }

                    const theme = /(?:^|[\\/])images[\\/](light|dark)[\\/]/
                        .exec(asset.originalFileNames?.[0] ?? "")?.[1];

                    return theme === undefined
                        ? "[name][extname]"
                        : `icons/${theme}/[name][extname]`;
                },
                format: "es",
            },
        },
    },
});
