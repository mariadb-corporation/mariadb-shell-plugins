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

import { fileURLToPath } from "node:url";

import preact from "@preact/preset-vite";
import { defineConfig } from "vitest/config";

// Unit tests run in plain Node, so the `vscode` module - which only exists
// inside the extension host - is resolved to a hand written test double.
const vscodeMock = fileURLToPath(
    new URL("./src/test/mocks/vscode.ts", import.meta.url),
);

/**
 * Two projects, because the two halves of the extension run in different
 * places: the extension host is Node with a `vscode` module, and the
 * result view is a browser page with a DOM.
 */
export default defineConfig({
    test: {
        coverage: {
            provider: "v8",
            include: ["src/**/*.ts", "webview/src/**/*.tsx"],
            exclude: ["src/test/**", "webview/test/**"],
        },
        projects: [
            {
                resolve: {
                    alias: [{ find: /^vscode$/, replacement: vscodeMock }],
                },
                test: {
                    name: "extension",
                    environment: "node",
                    include: ["src/test/**/*.test.ts"],
                },
            },
            {
                plugins: [preact()],
                test: {
                    name: "webview",
                    environment: "jsdom",
                    include: ["webview/test/**/*.test.tsx"],
                },
            },
        ],
    },
});
