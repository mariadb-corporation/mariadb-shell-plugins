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

import { describe, expect, it } from "vitest";

import webviewConfig from "../../../vite.webview.config.js";

/**
 * The result view loads its stylesheet through `asWebviewUri`, which
 * gives an absolute URL under the webview's own origin. Anything the
 * stylesheet then points at - the codicon font, above all - has to be
 * relative to it, or it resolves against that origin's root and 404s
 * with nothing reported: the icons simply come out blank.
 */
describe("the webview build", () => {
    it("emits relative asset URLs", () => {
        expect(webviewConfig.base).toBe("./");
    });

    it("keeps the stylesheet's name fixed and the font's own", () => {
        const output = webviewConfig.build?.rollupOptions?.output as {
            assetFileNames?: (asset: { names?: string[] }) => string;
        };

        // The extension references main.css by name from the HTML it
        // builds, while the font is referenced by the stylesheet under
        // the name the stylesheet was written with.
        expect(output.assetFileNames?.({ names: ["style.css"] }))
            .toBe("main.css");
        expect(output.assetFileNames?.({ names: ["codicon.ttf"] }))
            .toBe("[name][extname]");
    });
});
