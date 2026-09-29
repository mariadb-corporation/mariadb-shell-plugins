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

import {
    ServerStarter,
    type ServerPhase,
    type ServerStartSteps,
} from "../../mcp/serverStarter.js";
import type { IMariaDbApi } from "../../mcp/types.js";
import { createFakeApi, createRecordingLog } from "../helpers.js";

describe("ServerStarter", () => {
    it("runs the steps once for callers that ask at the same time",
        async () => {
            let runs = 0;
            let finish: (api: IMariaDbApi) => void = () => { /* set below */ };
            const steps: ServerStartSteps = () => {
                runs += 1;

                return new Promise((resolve) => {
                    finish = resolve;
                });
            };
            const starter = new ServerStarter(steps, createRecordingLog());

            // The tree, the toolbar and the panel, all at once: one
            // installer, not three into the same directory.
            const first = starter.start();
            const second = starter.start();
            const api = createFakeApi();
            finish(api);

            await expect(first).resolves.toBe(api);
            await expect(second).resolves.toBe(api);
            expect(runs).toBe(1);
        });

    it("reports each phase it goes through", async () => {
        const phases: ServerPhase[] = [];
        const starter = new ServerStarter(async (onPhase) => {
            onPhase("installing");
            onPhase("starting");

            return await Promise.resolve(createFakeApi());
        }, createRecordingLog());
        starter.onDidChangePhase((phase) => { phases.push(phase); });

        await starter.start();

        expect(phases).toEqual(["locating", "installing", "starting", "ready"]);
        expect(starter.phase).toBe("ready");
    });

    it("logs a failure, says it failed, and tries again when asked",
        async () => {
            const log = createRecordingLog();
            let runs = 0;
            const starter = new ServerStarter(() => {
                runs += 1;

                return Promise.reject(new Error("no network"));
            }, log);

            await expect(starter.start()).rejects.toThrow("no network");
            expect(starter.phase).toBe("failed");
            expect(log.lines).toEqual(
                ["The MCP server could not be started: no network"]);

            await expect(starter.start()).rejects.toThrow("no network");
            expect(runs).toBe(2);
        });

    it("stops telling a listener that has let go", async () => {
        const phases: ServerPhase[] = [];
        const starter = new ServerStarter(() => {
            return Promise.resolve(createFakeApi());
        }, createRecordingLog());
        const stop = starter.onDidChangePhase((phase) => {
            phases.push(phase);
        });
        stop();

        await starter.start();
        starter.stopped();

        expect(phases).toEqual([]);
        expect(starter.phase).toBe("stopped");
    });
});
