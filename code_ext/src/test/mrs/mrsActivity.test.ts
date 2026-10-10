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

import type { IActivityEvent } from "../../connections/connectionActivity.js";
import { describeRestSql } from "../../mrs/mrsActions.js";
import { reportMrsAction } from "../../mrs/mrsActivity.js";
import type { IActionRow } from "../../webview/protocol.js";

const URI = "mariadb://root@h:3306";
const when = new Date(2026, 9, 10, 12, 0, 0);

const recording = (logAllCalls = false) => {
    const events: IActivityEvent[] = [];
    const rows: IActionRow[] = [];
    const target = {
        sessionOf: (connectionId: string) => {
            return connectionId === "uuid-1"
                ? { uri: URI, label: "UI Backend" } : undefined;
        },
        report: (event: IActivityEvent) => { events.push(event); },
        put: (_connection: string, row: IActionRow) => { rows.push(row); },
        logAllCalls: () => { return logAllCalls; },
    };

    return { target, events, rows };
};

describe("MRS actions in words", () => {
    it("says what a statement did, by the one that matters", () => {
        expect(describeRestSql(["USE REST METADATA SCHEMA `m`;",
            "CREATE REST SERVICE /myService/v1 COMMENTS 'x';",
            "LINK REST AUTH APP 'MRS' TO SERVICE /myService/v1;"]))
            .toBe("Created REST Service '/myService/v1'");
        expect(describeRestSql(["CREATE OR REPLACE REST VIEW /city ON "
            + "SERVICE /a SCHEMA /s AS uitest.city"]))
            .toBe("Saved REST View '/city'");
        expect(describeRestSql(["ALTER REST SERVICE /a NEW REQUEST PATH /b"]))
            .toBe("Changed REST Service '/a'");
        expect(describeRestSql(["DROP REST CONTENT SET /web ON SERVICE /a"]))
            .toBe("Deleted REST Content Set '/web'");
        expect(describeRestSql(
            ["CREATE REST AUTH APP IF NOT EXISTS 'Google' VENDOR Google"]))
            .toBe("Created REST Authentication App 'Google'");
        expect(describeRestSql(["CREATE REST USER 'admin'@'MRS' "
            + "IDENTIFIED BY 'x'"])).toBe("Created REST User 'admin'@'MRS'");
        expect(describeRestSql(["SHOW REST SERVICES FORMAT=JSON"]))
            .toBe("Read REST Services");
        expect(describeRestSql(["SHOW REST METADATA STATUS FORMAT=JSON"]))
            .toBe("Read REST Service Status");
        expect(describeRestSql(["SHOW CREATE REST SERVICE /a"]))
            .toBe("Read the definition of REST Service '/a'");
        expect(describeRestSql(["CONFIGURE REST METADATA ENABLED"]))
            .toBe("Configured REST Metadata");
        expect(describeRestSql(["SELECT 1"])).toBe("Ran a REST statement");
    });
});

describe("MRS in the Actions tab", () => {
    it("puts an action up as a row saying what was done", () => {
        const { target, events } = recording();

        reportMrsAction(target, {
            connectionId: "uuid-1", when, elapsedMs: 40,
            message: "Listed REST Service '/a'", detail: "2 schemas",
        });

        expect(events).toEqual([{
            connection: URI, label: "UI Backend", call: "2 schemas",
            when, elapsedMs: 40, message: "Listed REST Service '/a'",
        }]);
    });

    it("puts a failed action up with its error as the row under it, as a "
        + "SQL script's run does", () => {
        const { target, events, rows } = recording();

        reportMrsAction(target, {
            connectionId: "uuid-1", when, elapsedMs: 5,
            message: "Created REST Service '/a'", error: "Duplicate path",
        });

        expect(events).toEqual([]);
        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({
            role: "run", kind: "error", connection: URI,
            connectionLabel: "UI Backend",
            message: "Created REST Service '/a'",
            summary: "Finished with 1 error",
            jumpToRowId: rows[0].children?.[0].id,
        });
        expect(rows[0].children).toMatchObject([{
            role: "statement", kind: "error", message: "Duplicate path",
        }]);
    });

    it("leaves out what was done on a connection closed since", () => {
        const { target, events } = recording(true);

        reportMrsAction(target, { connectionId: "gone", when, elapsedMs: 1,
            message: "Listed REST Services" });

        expect(events).toEqual([]);
    });

    it("files work on files only under General Actions, where that is "
        + "logged", () => {
        const action = {
            when, elapsedMs: 2, message: "Looked for MRS scripts in /p",
            detail: "TypeScript",
        };
        const quiet = recording(false);
        reportMrsAction(quiet.target, action);
        expect(quiet.events).toEqual([]);

        const logged = recording(true);
        reportMrsAction(logged.target, action);
        expect(logged.events[0]).toMatchObject({
            connection: "General Actions", label: "", call: "TypeScript",
        });
    });
});
