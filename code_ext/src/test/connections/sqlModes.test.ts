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

import { beforeEach, describe, expect, it } from "vitest";

import { ConnectionManager } from "../../connections/connectionManager.js";
import { hasAnsiQuotes, SqlModes } from "../../connections/sqlModes.js";
import { createFakeApi, createFakeSettings, createRecordingLog } from
    "../helpers.js";
import { resetVscodeMock } from "../mocks/vscode.js";

const DBA = "dba@localhost:3310";

/** The cache, over a fake server with the given sql_mode. */
const createModes = (sqlMode: string) => {
    const api = createFakeApi({
        connections: [DBA],
        connectionIds: { [DBA]: "uuid-dba" },
        sqlMode,
    });
    const connections = new ConnectionManager(
        () => {
            return Promise.resolve(api);
        },
        createFakeSettings(),
    );
    const log = createRecordingLog();
    const modes = new SqlModes(connections, log);
    const changed: string[] = [];
    modes.onDidChange((uri) => {
        changed.push(uri);
    });

    return { api, connections, modes, log, changed };
};

describe("hasAnsiQuotes", () => {
    it("finds the mode among the others, whatever the case", () => {
        expect(hasAnsiQuotes("ANSI_QUOTES")).toBe(true);
        expect(hasAnsiQuotes("STRICT_TRANS_TABLES,ansi_quotes,NO_ZERO_DATE"))
            .toBe(true);
        expect(hasAnsiQuotes("")).toBe(false);
        expect(hasAnsiQuotes("STRICT_TRANS_TABLES")).toBe(false);
        // Only the mode itself: the server spells ANSI out as its parts.
        expect(hasAnsiQuotes("ANSI")).toBe(false);
    });
});

describe("SqlModes", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("knows nothing, and asks nothing, about a connection not open",
        async () => {
            const { modes, api } = createModes("ANSI_QUOTES");

            expect(modes.known(DBA)).toBeUndefined();
            expect(await modes.ansiQuotes(DBA)).toBe(false);
            expect(api.statements).toEqual([]);
        });

    it("reads an open connection once, two asking at once included",
        async () => {
            const { connections, modes, api, changed } =
                createModes("STRICT_TRANS_TABLES,ANSI_QUOTES");
            await connections.connect(DBA);

            const [first, second] = await Promise.all([
                modes.ansiQuotes(DBA), modes.ansiQuotes(DBA),
            ]);
            expect([first, second]).toEqual([true, true]);
            expect(await modes.ansiQuotes(DBA)).toBe(true);
            expect(modes.known(DBA)).toBe(true);
            expect(api.statements.map((s) => { return s.sql; }))
                .toEqual(["SELECT @@sql_mode"]);
            expect(changed).toEqual([DBA]);
        });

    it("takes what a run reported, and says so only when it is news",
        async () => {
            const { connections, modes, api, changed } = createModes("");
            await connections.connect(DBA);

            modes.record(DBA, "ANSI_QUOTES");
            modes.record(DBA, "ANSI_QUOTES,STRICT_TRANS_TABLES");
            expect(changed).toEqual([DBA]);
            expect(await modes.ansiQuotes(DBA)).toBe(true);

            modes.record(DBA, "STRICT_TRANS_TABLES");
            expect(changed).toEqual([DBA, DBA]);
            expect(modes.known(DBA)).toBe(false);
            // Never read: the runs said it all.
            expect(api.statements).toEqual([]);
        });

    it("lets a run's report win over a read it overtook", async () => {
        const { connections, modes } = createModes("");
        await connections.connect(DBA);

        const reading = modes.ansiQuotes(DBA);
        modes.record(DBA, "ANSI_QUOTES");

        expect(await reading).toBe(true);
        expect(modes.known(DBA)).toBe(true);
    });

    it("forgets a connection when it closes", async () => {
        const { connections, modes, api } = createModes("ANSI_QUOTES");
        await connections.connect(DBA);
        await modes.ansiQuotes(DBA);

        await connections.disconnect(DBA);
        expect(modes.known(DBA)).toBeUndefined();

        await connections.connect(DBA);
        await modes.ansiQuotes(DBA);
        expect(api.statements).toHaveLength(2);
    });

    it("logs a failed read, answers false and asks again next time",
        async () => {
            const { connections, modes, api, log } =
                createModes("ANSI_QUOTES");
            await connections.connect(DBA);
            // A flag, not a swap back: the manager keeps the API it got.
            let failing = true;
            const read = api.executeSql;
            api.executeSql = (...args) => {
                return failing
                    ? Promise.reject(new Error("gone"))
                    : read(...args);
            };

            expect(await modes.ansiQuotes(DBA)).toBe(false);
            expect(modes.known(DBA)).toBeUndefined();
            expect(log.lines.some((line) => {
                return line.includes("Could not read sql_mode")
                    && line.includes("gone");
            })).toBe(true);

            failing = false;
            expect(await modes.ansiQuotes(DBA)).toBe(true);
        });
});
