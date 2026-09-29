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
 * Builds the New Sandbox dialog's Preact frontend.
 *
 * A build of its own for the reason the connection editor's is (see
 * `vite.editor.config.ts`): two entries in one build would share a hoisted
 * chunk, and a webview's script nonce does not extend to a module its entry
 * imports. It reuses the editor's stylesheet by importing it, which puts a
 * copy in its own `sandbox.css` rather than a shared file.
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
        // False, as for the editor's build: the result panel's runs first
        // and clears the directory.
        emptyOutDir: false,
        sourcemap: true,
        target: "es2022",
        rollupOptions: {
            input: "webview/src/sandbox.tsx",
            output: {
                entryFileNames: "sandbox.js",
                // The stylesheet has a fixed name because the extension
                // references it from the webview's HTML. Everything else
                // - the codicon font, above all - keeps its own name,
                // since the stylesheet points at it by that.
                assetFileNames: (asset) => {
                    return asset.names?.[0]?.endsWith(".css") ?? false
                        ? "sandbox.css"
                        : "[name][extname]";
                },
                format: "es",
            },
        },
    },
});
