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
 * Builds the schema model viewer's Preact frontend.
 *
 * A build of its own, like the other dialogs' (see `vite.editor.config.ts`
 * for why one build per entry): and this one bundles three.js, which no
 * other view needs.
 */
export default defineConfig({
    root: "webview",
    base: "./",
    plugins: [preact()],
    build: {
        outDir: "../dist/webview",
        emptyOutDir: false,
        sourcemap: true,
        target: "es2022",
        // three.js is large; the warning about it says nothing new.
        chunkSizeWarningLimit: 1500,
        rollupOptions: {
            input: "webview/src/modeler.tsx",
            output: {
                entryFileNames: "modeler.js",
                assetFileNames: (asset) => {
                    return asset.names?.[0]?.endsWith(".css") ?? false
                        ? "modeler.css"
                        : "[name][extname]";
                },
                format: "es",
            },
        },
    },
});
