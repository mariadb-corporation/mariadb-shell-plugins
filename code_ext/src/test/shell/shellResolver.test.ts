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

import type { BootstrapResult } from "../../shell/bootstrap.js";
import type { ShellLocation } from "../../shell/locator.js";
import { ShellResolver } from "../../shell/shellResolver.js";

const location = (): ShellLocation => {
    return {
        binaryPath: "/opt/mariadb-shell",
        version: { major: 26, minor: 10, patch: 1 },
        source: "managed",
    };
};

describe("ShellResolver", () => {
    it("runs one lookup for callers that ask at the same time", async () => {
        let runs = 0;
        let finish: (result: BootstrapResult) => void = () => { /* below */ };
        const resolver = new ShellResolver(() => {
            runs += 1;

            return new Promise((resolve) => {
                finish = resolve;
            });
        });

        const first = resolver.ensure();
        const second = resolver.ensure();
        finish({ location: location(), installed: false });

        expect(await first).toEqual(await second);
        expect(runs).toBe(1);
    });

    it("looks again once the last lookup is over", async () => {
        let runs = 0;
        const resolver = new ShellResolver(() => {
            runs += 1;

            return Promise.resolve({ location: location(), installed: false });
        });

        await resolver.ensure();
        await resolver.ensure();

        expect(runs).toBe(2);
    });

    it("tells every caller that the installer runs, joiners included",
        async () => {
            let install: () => void = () => { /* set below */ };
            let finish: (result: BootstrapResult) => void = () => { /* below */ };
            const resolver = new ShellResolver((onInstalling) => {
                install = onInstalling;

                return new Promise((resolve) => {
                    finish = resolve;
                });
            });
            const told: string[] = [];

            const first = resolver.ensure(() => { told.push("first"); });
            install();
            const late = resolver.ensure(() => { told.push("late"); });
            finish({ location: location(), installed: true });
            await Promise.all([first, late]);

            expect(told).toEqual(["first", "late"]);
        });

    it("lets a failed lookup be tried again", async () => {
        let runs = 0;
        const resolver = new ShellResolver(() => {
            runs += 1;

            return runs === 1
                ? Promise.reject(new Error("no network"))
                : Promise.resolve({ location: location(), installed: true });
        });

        await expect(resolver.ensure()).rejects.toThrow("no network");
        expect((await resolver.ensure()).installed).toBe(true);
    });
});
