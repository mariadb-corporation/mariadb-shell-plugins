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
import { SqlModes } from "../../connections/sqlModes.js";
import { AnsiQuotesHighlighter } from "../../editor/ansiQuotes.js";
import {
    isSqlDocument,
    MARIADB_SQL_LANGUAGE_ID,
} from "../../editor/sqlLanguage.js";
import { createFakeApi, createFakeSettings, createRecordingLog } from
    "../helpers.js";
import {
    configuration,
    fireConfigurationChange,
    MockTextDocument,
    resetVscodeMock,
    semanticTokensProviders,
    type MockSemanticToken,
    Uri,
} from "../mocks/vscode.js";

const DBA = "dba@localhost:3310";

const notCancelled = {
    isCancellationRequested: false,
    onCancellationRequested: () => {
        return { dispose: () => { /* nothing to undo */ } };
    },
};

/** A SQL document with the given text. */
const createDocument = (text: string, name = "query.sql") => {
    return new MockTextDocument(Uri.file(`/work/${name}`),
        MARIADB_SQL_LANGUAGE_ID, text);
};

/** The highlighter, over a fake server with the given sql_mode. */
const createHighlighter = (sqlMode: string) => {
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
    const sqlModes = new SqlModes(connections, log);
    // Every document in these tests runs on DBA unless it is called
    // unbound.sql.
    const highlighter = new AnsiQuotesHighlighter(connections, sqlModes,
        (document) => {
            return document.uri.path.endsWith("unbound.sql")
                ? undefined
                : DBA;
        });
    const tokensOf = async (document: MockTextDocument) => {
        const tokens = await highlighter.provideDocumentSemanticTokens(
            document as never, notCancelled);

        return (tokens as unknown as { tokens: MockSemanticToken[] }).tokens;
    };

    return { api, connections, highlighter, log, sqlModes, tokensOf };
};

describe("isSqlDocument", () => {
    it("takes the MariaDB language and VS Code's own", () => {
        for (const id of [MARIADB_SQL_LANGUAGE_ID, "sql"]) {
            expect(isSqlDocument(new MockTextDocument(Uri.file("/a.sql"), id,
                "") as never), id).toBe(true);
        }
        expect(isSqlDocument(new MockTextDocument(Uri.file("/a.txt"),
            "plaintext", "") as never)).toBe(false);
    });
});

describe("AnsiQuotesHighlighter", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("registers for both SQL languages", () => {
        const { highlighter } = createHighlighter("");

        expect(semanticTokensProviders).toHaveLength(1);
        expect(semanticTokensProviders[0].selector).toEqual([
            { language: "mariadb-sql" }, { language: "sql" },
        ]);
        expect(semanticTokensProviders[0].legend.tokenTypes)
            .toEqual(["sqlQuotedIdentifier"]);
        highlighter.dispose();
        expect(semanticTokensProviders).toHaveLength(0);
    });

    it("marks every \"...\" when the connection has ANSI_QUOTES",
        async () => {
            const { connections, tokensOf, api } = createHighlighter(
                "STRICT_TRANS_TABLES,ANSI_QUOTES");
            await connections.connect(DBA);

            expect(await tokensOf(createDocument(
                "SELECT \"a\" FROM \"t\" WHERE x = 'no \"b\"';")))
                .toEqual([
                    { line: 0, start: 7, length: 3 },
                    { line: 0, start: 16, length: 3 },
                ]);
            expect(api.statements.map((s) => { return s.sql; }))
                .toEqual(["SELECT @@sql_mode"]);
        });

    it("splits a span over lines into a token per line", async () => {
        const { connections, tokensOf } = createHighlighter("ANSI_QUOTES");
        await connections.connect(DBA);

        expect(await tokensOf(createDocument("SELECT \"a\nbc\nd\" x")))
            .toEqual([
                { line: 0, start: 7, length: 2 },
                { line: 1, start: 0, length: 2 },
                { line: 2, start: 0, length: 2 },
            ]);
    });

    it("marks nothing without ANSI_QUOTES", async () => {
        const { connections, tokensOf } = createHighlighter(
            "STRICT_TRANS_TABLES");
        await connections.connect(DBA);

        expect(await tokensOf(createDocument("SELECT \"a\";"))).toEqual([]);
    });

    it("marks nothing, and asks nothing, until the connection is open",
        async () => {
            const { tokensOf, api } = createHighlighter("ANSI_QUOTES");

            expect(await tokensOf(createDocument("SELECT \"a\";")))
                .toEqual([]);
            expect(await tokensOf(createDocument("SELECT \"a\";",
                "unbound.sql"))).toEqual([]);
            expect(api.statements).toEqual([]);
        });

    it("asks a connection once, and again after it was closed",
        async () => {
            const { connections, tokensOf, api } =
                createHighlighter("ANSI_QUOTES");
            await connections.connect(DBA);

            await tokensOf(createDocument("\"a\"", "a.sql"));
            await tokensOf(createDocument("\"b\"", "b.sql"));
            expect(api.statements).toHaveLength(1);

            await connections.disconnect(DBA);
            await connections.connect(DBA);
            await tokensOf(createDocument("\"c\"", "c.sql"));
            expect(api.statements).toHaveLength(2);
        });

    it("follows the setting instead when it is not auto", async () => {
        const { tokensOf, api } = createHighlighter("");
        const document = createDocument("SELECT \"a\";", "unbound.sql");

        configuration.set("mariadb.sql.ansiQuotes", "on");
        expect(await tokensOf(document)).toEqual([
            { line: 0, start: 7, length: 3 },
        ]);

        configuration.set("mariadb.sql.ansiQuotes", "off");
        expect(await tokensOf(document)).toEqual([]);

        // Neither needed a connection.
        expect(api.statements).toEqual([]);
    });

    it("asks for new tokens when a connection opens or the setting changes",
        async () => {
            const { connections, highlighter } = createHighlighter("");
            let asked = 0;
            highlighter.onDidChangeSemanticTokens(() => {
                asked += 1;
            });

            await connections.connect(DBA);
            expect(asked).toBe(1);

            fireConfigurationChange("mariadb.sql.ansiQuotes");
            expect(asked).toBe(2);

            fireConfigurationChange("mariadb.execute.pageSize");
            expect(asked).toBe(2);

            highlighter.refresh();
            expect(asked).toBe(3);
        });

    it("follows a sql_mode a run reported, without asking", async () => {
        const { connections, highlighter, sqlModes, tokensOf, api } =
            createHighlighter("");
        await connections.connect(DBA);
        let asked = 0;
        highlighter.onDidChangeSemanticTokens(() => {
            asked += 1;
        });

        sqlModes.record(DBA, "ANSI_QUOTES,STRICT_TRANS_TABLES");

        expect(asked).toBe(1);
        expect(await tokensOf(createDocument("SELECT \"a\";"))).toEqual([
            { line: 0, start: 7, length: 3 },
        ]);
        expect(api.statements).toEqual([]);
    });

    it("logs a failed read and marks nothing", async () => {
        const { connections, tokensOf, api, log } =
            createHighlighter("ANSI_QUOTES");
        await connections.connect(DBA);
        api.executeSql = () => {
            return Promise.reject(new Error("gone"));
        };

        expect(await tokensOf(createDocument("SELECT \"a\";"))).toEqual([]);
        expect(log.lines.some((message) => {
            return message.includes("Could not read sql_mode")
                && message.includes("gone");
        })).toBe(true);
    });
});
