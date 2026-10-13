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
    describeRestSql,
    describeStatement,
    type IMrsAction,
} from "../../mrs/mrsActions.js";
import { reportMrsAction } from "../../mrs/mrsActivity.js";
import type { IActionRow } from "../../webview/protocol.js";

const URI = "mariadb://root@h:3306";
const when = new Date(2026, 9, 10, 12, 0, 0);

const recording = (logAllCalls = false) => {
    const rows: IActionRow[] = [];
    const target = {
        sessionOf: (connectionId: string) => {
            return connectionId === "uuid-1"
                ? { uri: URI, label: "UI Backend" } : undefined;
        },
        put: (_connection: string, row: IActionRow) => { rows.push(row); },
        logAllCalls: () => { return logAllCalls; },
    };

    return { target, rows };
};

describe("MRS actions in words", () => {
    it("says what a single statement does, for the rows under an action",
        () => {
            expect(describeStatement("USE REST METADATA SCHEMA `m`;")?.done)
                .toBe("Switched to REST Metadata Schema 'm'");
            expect(describeStatement("USE REST SERVICE /a;")?.done)
                .toBe("Switched to REST Service '/a'");
            expect(describeStatement("SELECT 1")).toBeUndefined();
            expect(describeStatement("GRANT REST ROLE 'Full Access' ON ANY "
                + "SERVICE TO 'anna'@'UiApp';")?.done)
                .toBe("Granted REST Role 'Full Access'");
        });

    it("says what is being done while it runs", () => {
        expect(describeRestSql(["DROP REST USER 'u'@'MRS'"]).doing)
            .toBe("Deleting REST User 'u'@'MRS'\u2026");
        expect(describeRestSql(["GRANT REST ROLE 'r' TO 'u'@'MRS'"]).doing)
            .toBe("Granting REST Role 'r'\u2026");
    });

    it("says what a statement did, by the one that matters", () => {
        expect(describeRestSql(["USE REST METADATA SCHEMA `m`;",
            "CREATE REST SERVICE /myService/v1 COMMENTS 'x';",
            "LINK REST AUTH APP 'MRS' TO SERVICE /myService/v1;"]).done)
            .toBe("Created REST Service '/myService/v1'");
        expect(describeRestSql(["CREATE OR REPLACE REST VIEW /city ON "
            + "SERVICE /a SCHEMA /s AS uitest.city"]).done)
            .toBe("Saved REST View '/city'");
        expect(describeRestSql(["ALTER REST SERVICE /a NEW REQUEST PATH /b"]).done)
            .toBe("Changed REST Service '/a'");
        expect(describeRestSql(["DROP REST CONTENT SET /web ON SERVICE /a"]).done)
            .toBe("Deleted REST Content Set '/web'");
        expect(describeRestSql(
            ["CREATE REST AUTH APP IF NOT EXISTS 'Google' VENDOR Google"]).done)
            .toBe("Created REST Authentication App 'Google'");
        expect(describeRestSql(["CREATE REST USER 'admin'@'MRS' "
            + "IDENTIFIED BY 'x'"]).done)
            .toBe("Created REST User 'admin'@'MRS'");
        expect(describeRestSql(["SHOW REST SERVICES FORMAT=JSON"]).done)
            .toBe("Read REST Services");
        expect(describeRestSql(["SHOW REST METADATA STATUS FORMAT=JSON"]).done)
            .toBe("Read REST Service Status");
        expect(describeRestSql(["SHOW CREATE REST SERVICE /a"]).done)
            .toBe("Read the definition of REST Service '/a'");
        expect(describeRestSql(["CONFIGURE REST METADATA ENABLED"]).done)
            .toBe("Configured REST Metadata");
        expect(describeRestSql(["SELECT 1"]).done)
            .toBe("Ran a REST statement");
    });
});

describe("MRS in the Actions tab", () => {
    const action = (overrides: Partial<IMrsAction> = {}): IMrsAction => {
        return {
            id: "mrs-sql-1", connectionId: "uuid-1", when, elapsedMs: 9,
            message: "Created REST Service '/a'", ...overrides,
        };
    };

    it("puts up a running action with the spinner, and replaces it once "
        + "done", () => {
        const { target, rows } = recording();

        reportMrsAction(target, action({ pending: true, elapsedMs: 0,
            message: "Creating REST Service '/a'\u2026" }));
        reportMrsAction(target, action({ detail: "linked to MRS" }));

        expect(rows).toEqual([{
            id: "mrs-sql-1", time: "12:00:00.000", connection: URI,
            connectionLabel: "UI Backend", role: "event", statement: "",
            kind: "pending", message: "Creating REST Service '/a'\u2026",
        }, {
            id: "mrs-sql-1", time: "12:00:00.000", connection: URI,
            connectionLabel: "UI Backend", role: "event",
            statement: "linked to MRS", kind: "info", elapsedMs: 9,
            message: "Created REST Service '/a'",
        }]);
    });

    it("puts a failed action up with its error as the row under it, as a "
        + "SQL script's run does", () => {
        const { target, rows } = recording();

        reportMrsAction(target, action({ error: "Duplicate path" }));

        expect(rows[0]).toMatchObject({
            role: "run", kind: "error", message: "Created REST Service '/a'",
            summary: "Finished with 1 error", jumpToRowId: "mrs-sql-1-error",
        });
        expect(rows[0].children).toMatchObject([{
            id: "mrs-sql-1-error", role: "statement", kind: "error",
            message: "Duplicate path",
        }]);
    });

    it("puts each statement under the action where every call is logged",
        () => {
            const { target, rows } = recording();

            reportMrsAction(target, action({ statements: [
                { sql: "USE REST METADATA SCHEMA `m`;",
                    message: "Query OK, 0 rows affected" },
                { sql: "CREATE REST SERVICE /a;",
                    message: "Query OK, 1 row affected", elapsedMs: 3 },
            ] }));

            expect(rows[0]).toMatchObject({
                role: "run", kind: "info",
                summary: "Finished 2 statements successfully",
            });
            expect(rows[0].children?.map((row) => {
                return [row.statement, row.message, row.elapsedMs];
            })).toEqual([
                ["USE REST METADATA SCHEMA `m`;", "Query OK, 0 rows affected",
                    undefined],
                ["CREATE REST SERVICE /a;", "Query OK, 1 row affected", 3],
            ]);
        });

    it("shows a single statement beside the action, not under it", () => {
        const { target, rows } = recording();

        reportMrsAction(target, action({ statements: [
            { sql: "CREATE REST SERVICE /a;",
                message: "Created REST Service '/a'", elapsedMs: 3 },
        ] }));

        expect(rows).toEqual([{
            id: "mrs-sql-1", time: "12:00:00.000", connection: URI,
            connectionLabel: "UI Backend", role: "event", kind: "info",
            statement: "CREATE REST SERVICE /a;", elapsedMs: 9,
            message: "Created REST Service '/a'",
        }]);
    });

    it("points a failure at the statement that failed, where they are "
        + "logged", () => {
        const { target, rows } = recording();

        reportMrsAction(target, action({ error: "Duplicate", statements: [
            { sql: "CREATE REST SCHEMA /s;", message: "Duplicate",
                error: "Duplicate" },
        ] }));

        expect(rows[0].jumpToRowId).toBe("mrs-sql-1-0");
        expect(rows[0].children).toHaveLength(1);
    });

    it("leaves out what was done on a connection closed since", () => {
        const { target, rows } = recording(true);

        reportMrsAction(target, action({ connectionId: "gone" }));

        expect(rows).toEqual([]);
    });

    it("files work on files only under General Actions, where every call "
        + "is logged", () => {
        const onFiles = action({ connectionId: undefined, detail: "none",
            message: "Looked for MRS scripts in /p" });
        const quiet = recording(false);
        reportMrsAction(quiet.target, onFiles);
        expect(quiet.rows).toEqual([]);

        const logged = recording(true);
        reportMrsAction(logged.target, onFiles);
        expect(logged.rows[0]).toMatchObject({
            connection: "General Actions", connectionLabel: "",
            statement: "none",
        });
    });
});
