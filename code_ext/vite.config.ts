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

import { builtinModules } from "node:module";
import { defineConfig } from "vite";

// The extension host loads a single CommonJS file and provides the `vscode`
// module itself, so everything except `vscode` and the Node built-ins is
// bundled in.
const external = [
    "vscode",
    ...builtinModules,
    ...builtinModules.map((name) => `node:${name}`),
];

export default defineConfig({
    build: {
        target: "node20",
        outDir: "dist",
        sourcemap: true,
        minify: false,
        // The webview build writes into dist/webview, and this build runs
        // again on every change in watch mode. Emptying dist here would
        // delete the panel's assets out from under a running extension
        // host, so `npm run clean` owns wiping dist instead.
        emptyOutDir: false,
        lib: {
            entry: "src/extension.ts",
            formats: ["cjs"],
            fileName: () => "extension.js",
        },
        rollupOptions: {
            external,
            output: {
                interop: "auto",
            },
        },
    },
});
