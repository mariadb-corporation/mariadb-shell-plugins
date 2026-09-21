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
import {
    buildConnectionHeader,
    readConnectionHeader,
    SqlEditorBinding,
} from "../../editor/sqlEditorBinding.js";
import type { IViewState } from "../../webview/protocol.js";
import {
    ResultViewProvider,
    RESULT_VIEW_ID,
} from "../../webview/resultViewProvider.js";
import { createFakeApi, createFakeSettings, createRecordingLog } from
    "../helpers.js";
import {
    configuration,
    contextKeys,
    fireActiveEditorChange,
    fireDocumentClose,
    informationMessages,
    MockTextDocument,
    MockWebviewView,
    Position,
    Selection,
    quickPickAnswers,
    quickPickCalls,
    resetVscodeMock,
    openedDocuments,
    shownDocuments,
    shownEditors,
    statusBarItems,
    Uri,
    warningMessages,
} from "../mocks/vscode.js";

/**
 * @param text The document's contents.
 * @param languageId Its language.
 *
 * @returns A stand-in for a text editor over that document.
 */
const createEditor = (text: string, languageId = "sql") => {
    const document = new MockTextDocument(
        Uri.file("/work/query.sql"), languageId, text);

    return {
        document: Object.assign(document, {
            // The binding asks for the selection's text; the mock
            // document has no notion of one, so it is answered here.
            getText: (range?: unknown) => {
                return range === undefined ? text : "SELECT 2;";
            },
        }),
        // Empty by construction: anchor and active are the same spot.
        selection: new Selection(new Position(0, 0), new Position(0, 0)),
    };
};

/**
 * @param defaultConnection The default connection, if any.
 *
 * @returns A binding over a fake server.
 */
const createBinding = (defaultConnection?: string) => {
    const api = createFakeApi({
        connections: ["dba@localhost:3310", "app@localhost:3311"],
        connectionIds: {
            "dba@localhost:3310": "uuid-dba",
            "app@localhost:3311": "uuid-app",
        },
        defaultResults: [{
            affected_items_count: 0,
            warnings_count: 0,
            columns: ["a"],
            rows: [{ a: 1 }],
        }],
    });
    const connections = new ConnectionManager(
        () => {
            return Promise.resolve(api);
        },
        createFakeSettings(defaultConnection),
    );
    const log = createRecordingLog();
    const resultView = new ResultViewProvider(Uri.file("/ext") as never, log);
    const view = new MockWebviewView(RESULT_VIEW_ID);
    resultView.resolveWebviewView(view as never);
    view.webview.receive({ type: "ready" });
    const binding = new SqlEditorBinding(connections, resultView, log);

    return { api, connections, resultView, view, binding, log };
};

describe("SqlEditorBinding", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("hides the status bar entry when no SQL editor is active", () => {
        const { binding } = createBinding();

        expect(statusBarItems[0].visible).toBe(false);

        binding.dispose();
    });

    it("shows the default connection for a SQL editor", () => {
        const { binding } = createBinding("dba@localhost:3310");
        fireActiveEditorChange(createEditor("SELECT 1;"));

        expect(statusBarItems[0].visible).toBe(true);
        expect(statusBarItems[0].text).toContain("dba@localhost:3310");

        binding.dispose();
    });

    it("says so when there is no connection to run on", () => {
        const { binding } = createBinding();
        fireActiveEditorChange(createEditor("SELECT 1;"));

        expect(statusBarItems[0].text).toContain("no connection");

        binding.dispose();
    });

    it("hides itself again for a non-SQL editor", () => {
        const { binding } = createBinding("dba@localhost:3310");
        fireActiveEditorChange(createEditor("SELECT 1;"));
        fireActiveEditorChange(createEditor("print()", "python"));

        expect(statusBarItems[0].visible).toBe(false);

        binding.dispose();
    });

    it("falls back to the default connection for a document", () => {
        const { binding } = createBinding("dba@localhost:3310");
        const editor = createEditor("SELECT 1;");

        expect(binding.connectionFor(editor.document as never))
            .toBe("dba@localhost:3310");

        binding.dispose();
    });

    it("lets a document be pinned to another connection", async () => {
        const { binding } = createBinding("dba@localhost:3310");
        const editor = createEditor("SELECT 1;");
        fireActiveEditorChange(editor);
        quickPickAnswers.push("app@localhost:3311");

        await expect(binding.selectConnection(editor.document as never))
            .resolves.toBe("app@localhost:3311");

        expect(binding.connectionFor(editor.document as never))
            .toBe("app@localhost:3311");
        expect(statusBarItems[0].text).toContain("app@localhost:3311");

        binding.dispose();
    });

    it("marks the default and the open connections in the picker",
        async () => {
            const { connections, binding } = createBinding(
                "dba@localhost:3310");
            await connections.connect("app@localhost:3311");
            quickPickAnswers.push("dba@localhost:3310");

            await binding.selectConnection(
                createEditor("SELECT 1;").document as never);

            expect(quickPickCalls[0]).toEqual([
                {
                    label: "dba@localhost:3310",
                    description: "default",
                    picked: true,
                },
                {
                    label: "app@localhost:3311",
                    description: "connected",
                    picked: false,
                },
            ]);

            binding.dispose();
        });

    it("keeps the current choice when the picker is cancelled", async () => {
        const { binding } = createBinding("dba@localhost:3310");
        const editor = createEditor("SELECT 1;");

        await expect(binding.selectConnection(editor.document as never))
            .resolves.toBeUndefined();
        expect(binding.connectionFor(editor.document as never))
            .toBe("dba@localhost:3310");

        binding.dispose();
    });

    it("warns when nothing is configured to connect to", async () => {
        const api = createFakeApi({ connections: [] });
        const connections = new ConnectionManager(
            () => {
                return Promise.resolve(api);
            },
            createFakeSettings(),
        );
        const log = createRecordingLog();
        const binding = new SqlEditorBinding(
            connections,
            new ResultViewProvider(Uri.file("/ext") as never, log),
            log);

        await expect(binding.selectConnection(
            createEditor("SELECT 1;").document as never,
        )).resolves.toBeUndefined();
        expect(warningMessages[0]).toContain("mcp setup");

        binding.dispose();
    });

    it("runs the file and shows the results in the view", async () => {
        const { api, view, binding } = createBinding("dba@localhost:3310");
        const editor = createEditor("SELECT 1 AS a;");

        await binding.run(editor as never);

        expect(api.scripts).toEqual(["SELECT 1 AS a;"]);
        const posted = view.webview.posted as Array<{
            type: string;
            state?: { actions: Array<{ kind: string; summary?: string }> };
        }>;
        // Two states: the run going up before anything is run, and the
        // same row with what it produced in it. The state holds the
        // provider's own array, so the first message cannot be read back
        // once the second has changed it - what it carried is pinned in
        // the provider's own tests.
        const states = posted.filter((message) => {
            return message.type === "state";
        });
        expect(states).toHaveLength(2);
        expect(states.at(-1)?.state?.actions).toHaveLength(1);
        expect(states.at(-1)?.state?.actions.at(-1)).toMatchObject({
            kind: "info",
            summary: "Finished 1 statement successfully",
        });

        binding.dispose();
    });

    it("runs only the selection when there is one", async () => {
        const { api, binding } = createBinding("dba@localhost:3310");
        const editor = {
            ...createEditor("SELECT 1;\nSELECT 2;"),
            // The second line, which the document's getText answers for
            // any range with "SELECT 2;".
            selection: new Selection(
                new Position(1, 0), new Position(1, 9)),
        };

        await binding.run(editor as never);

        expect(api.scripts).toEqual(["SELECT 2;"]);

        binding.dispose();
    });

    it("opens the connection it runs on", async () => {
        const { connections, binding } = createBinding("dba@localhost:3310");

        await binding.run(createEditor("SELECT 1;") as never);

        expect(connections.isConnected("dba@localhost:3310")).toBe(true);

        binding.dispose();
    });

    it("says which connection it ran on, on every row", async () => {
        const { view, binding } = createBinding("dba@localhost:3310");

        await binding.run(createEditor("SELECT 1;") as never);

        // An editor runs on a numbered connection of its own; the
        // Connections view browses on the one it keeps beside it.
        const states = view.webview.posted.filter((message) => {
            return (message as { type: string }).type === "state";
        }) as Array<{ state: IViewState }>;
        const run = states.at(-1)?.state.actions.at(-1);
        expect(run?.connectionLabel).toBe("1");
        expect(run?.children?.[0].connectionLabel).toBe("1");

        binding.dispose();
    });

    it("asks which connection to use when there is no default",
        async () => {
            const { api, binding } = createBinding();
            quickPickAnswers.push("app@localhost:3311");

            await binding.run(createEditor("SELECT 1;") as never);

            expect(quickPickCalls).toHaveLength(1);
            expect(api.scripts).toEqual(["SELECT 1;"]);

            binding.dispose();
        });

    it("runs nothing when the pick is cancelled", async () => {
        const { api, binding } = createBinding();

        await binding.run(createEditor("SELECT 1;") as never);

        expect(api.scripts).toEqual([]);

        binding.dispose();
    });

    it("runs nothing for an empty file", async () => {
        const { api, binding } = createBinding("dba@localhost:3310");

        await binding.run(createEditor("   \n  ") as never);

        expect(api.scripts).toEqual([]);

        binding.dispose();
    });

    it("shows a failure to connect in the view", async () => {
        const { api, view, binding } = createBinding("dba@localhost:3310");
        api.connect = () => {
            return Promise.reject(new Error("Access denied for user 'dba'"));
        };

        await binding.run(createEditor("SELECT 1;") as never);

        const posted = view.webview.posted as Array<{
            type: string;
            state?: {
                actions: Array<{
                    summary?: string;
                    kind: string;
                    children?: Array<{ message: string; kind: string }>;
                }>;
            };
        }>;
        const states = posted.filter((message) => {
            return message.type === "state";
        });
        // The run that was put up pending is closed off rather than left
        // running, and what the server said sits under it.
        const run = states.at(-1)?.state?.actions.at(-1);
        expect(run).toMatchObject({
            summary: "Execution failed: Access denied for user 'dba'",
            kind: "error",
        });
        expect(run?.children).toEqual([expect.objectContaining({
            message: "Access denied for user 'dba'",
            kind: "error",
        })]);

        binding.dispose();
    });

    it("follows the default connection when it changes", async () => {
        const { connections, binding } = createBinding();
        fireActiveEditorChange(createEditor("SELECT 1;"));
        expect(statusBarItems[0].text).toContain("no connection");

        await connections.setDefaultConnection("app@localhost:3311");

        expect(statusBarItems[0].text).toContain("app@localhost:3311");

        binding.dispose();
    });

    it("opens an unsaved SQL file bound to a connection", async () => {
        const { binding } = createBinding();

        const document = await binding.openSqlEditor("app@localhost:3311");

        expect(openedDocuments).toHaveLength(1);
        expect(openedDocuments[0].languageId).toBe("sql");
        expect(shownDocuments).toEqual([document]);
        expect(binding.connectionFor(document as never))
            .toBe("app@localhost:3311");

        binding.dispose();
    });

    it("puts the caret on the blank line below the header", async () => {
        const { binding } = createBinding();

        await binding.openSqlEditor("app@localhost:3311");

        // The content is the header plus two newlines, so the caret
        // belongs on the third line (index 2), ready to type on.
        const shown = shownEditors.at(-1);
        expect(shown?.selection.start).toEqual({ line: 2, character: 0 });
        expect(shown?.selection.isEmpty).toBe(true);

        binding.dispose();
    });

    it("records the connection in the new file's header", async () => {
        const { binding } = createBinding();

        const document = await binding.openSqlEditor("app@localhost:3311");

        expect(document.getText())
            .toBe("-- MariaDB connection: app@localhost:3311\n\n");

        binding.dispose();
    });

    it("binds the new file even against a different default", async () => {
        const { binding } = createBinding("dba@localhost:3310");

        const document = await binding.openSqlEditor("app@localhost:3311");

        expect(binding.connectionFor(document as never))
            .toBe("app@localhost:3311");

        binding.dispose();
    });

    it("runs the new file on the connection it was opened for",
        async () => {
            const { api, binding } = createBinding("dba@localhost:3310");
            const document = await binding.openSqlEditor(
                "app@localhost:3311");
            (document as unknown as MockTextDocument).setText(
                `${buildConnectionHeader("app@localhost:3311")}\n`
                + "SELECT 1;");

            await binding.run({
                document,
                selection: { isEmpty: true },
            } as never);

            expect(api.scripts).toEqual([
                "-- MariaDB connection: app@localhost:3311\nSELECT 1;",
            ]);
            // Opened on the bound connection, not on the default.
            expect(binding.connectionFor(document as never))
                .toBe("app@localhost:3311");

            binding.dispose();
        });

    it("shows the bound connection in the status bar", async () => {
        const { binding } = createBinding("dba@localhost:3310");

        const document = await binding.openSqlEditor("app@localhost:3311");
        fireActiveEditorChange({
            document,
            selection: { isEmpty: true },
        });

        expect(statusBarItems[0].text).toContain("app@localhost:3311");

        binding.dispose();
    });

    it("falls back to the header once the in-memory binding is gone",
        () => {
            // What happens after the file is saved: its URI changes, so
            // the binding keyed on the old one no longer matches.
            const { binding } = createBinding("dba@localhost:3310");
            const saved = new MockTextDocument(
                Uri.file("/work/report.sql"),
                "sql",
                `${buildConnectionHeader("app@localhost:3311")}\n`
                + "SELECT 1;",
            );

            expect(binding.connectionFor(saved as never))
                .toBe("app@localhost:3311");

            binding.dispose();
        });

    it("lets an explicit pick win over the header", async () => {
        const { binding } = createBinding();
        const document = await binding.openSqlEditor("app@localhost:3311");
        quickPickAnswers.push("dba@localhost:3310");

        await binding.selectConnection(document as never);

        expect(binding.connectionFor(document as never))
            .toBe("dba@localhost:3310");

        binding.dispose();
    });

    describe("running the statement at the cursor", () => {
        it("runs only that statement", async () => {
            const { api, binding } = createBinding("dba@localhost:3310");
            const editor = createEditor(
                "SELECT 1;\nSELECT 2;\nSELECT 3;");
            editor.selection = new Selection(
                new Position(1, 3), new Position(1, 3));

            await binding.runStatementAtCursor(editor as never);

            expect(api.scripts).toEqual(["SELECT 2"]);

            binding.dispose();
        });

        it("selects the statement it ran", async () => {
            const { binding } = createBinding("dba@localhost:3310");
            const editor = createEditor(
                "SELECT 1;\nSELECT 2;\nSELECT 3;");
            editor.selection = new Selection(
                new Position(2, 2), new Position(2, 2));

            await binding.runStatementAtCursor(editor as never);

            expect(editor.selection.start).toEqual(
                { line: 2, character: 0 });
            expect(editor.selection.end).toEqual(
                { line: 2, character: 8 });

            binding.dispose();
        });

        it("says so when the cursor is not in a statement", async () => {
            const { api, binding } = createBinding("dba@localhost:3310");
            const editor = createEditor("-- nothing but a note\n");
            editor.selection = new Selection(
                new Position(0, 3), new Position(0, 3));

            await binding.runStatementAtCursor(editor as never);

            expect(api.scripts).toEqual([]);
            expect(informationMessages.at(-1))
                .toContain("not in a statement");

            binding.dispose();
        });
    });

    describe("stopping on error", () => {
        it("starts a file off on the extension setting", () => {
            const { binding } = createBinding();
            const editor = createEditor("SELECT 1;");

            // The setting defaults to stopping.
            expect(binding.stopOnErrorFor(editor.document as never))
                .toBe(true);

            configuration.set("mariadb.execute.stopOnError", false);
            const other = createEditor("SELECT 2;");
            expect(binding.stopOnErrorFor(other.document as never))
                .toBe(false);

            binding.dispose();
        });

        it("lets one file differ from the setting", () => {
            const { binding } = createBinding();
            const editor = createEditor("SELECT 1;");

            expect(binding.toggleStopOnError(editor.document as never))
                .toBe(false);
            expect(binding.stopOnErrorFor(editor.document as never))
                .toBe(false);

            binding.dispose();
        });

        it("keeps the setting for files that were not switched", () => {
            const { binding } = createBinding();
            const switched = createEditor("SELECT 1;");
            const untouched = new MockTextDocument(
                Uri.file("/work/other.sql"), "sql", "SELECT 2;");

            binding.toggleStopOnError(switched.document as never);

            expect(binding.stopOnErrorFor(untouched as never)).toBe(true);

            binding.dispose();
        });

        it("toggles back again", () => {
            const { binding } = createBinding();
            const editor = createEditor("SELECT 1;");

            binding.toggleStopOnError(editor.document as never);

            expect(binding.toggleStopOnError(editor.document as never))
                .toBe(true);

            binding.dispose();
        });

        it("publishes the state as a context key for the toolbar", () => {
            const { binding } = createBinding();
            const editor = createEditor("SELECT 1;");
            fireActiveEditorChange(editor);

            // A button cannot change its own icon, so the toolbar shows
            // one of two commands depending on this.
            expect(contextKeys.get("mariadb.stopOnError")).toBe(true);

            binding.toggleStopOnError(editor.document as never);

            expect(contextKeys.get("mariadb.stopOnError")).toBe(false);

            binding.dispose();
        });

        it("runs the script the way the file is set", async () => {
            const { api, binding } = createBinding("dba@localhost:3310");
            const editor = createEditor("SELECT 1;");

            await binding.run(editor as never);
            expect(api.stopOnError).toBe(true);

            binding.toggleStopOnError(editor.document as never);
            await binding.run(editor as never);

            expect(api.stopOnError).toBe(false);

            binding.dispose();
        });

        it("forgets a closed file's choice", () => {
            const { binding } = createBinding();
            const editor = createEditor("SELECT 1;");
            binding.toggleStopOnError(editor.document as never);

            fireDocumentClose(editor.document as never);

            // Reopened, it starts from the setting again.
            expect(binding.stopOnErrorFor(editor.document as never))
                .toBe(true);

            binding.dispose();
        });
    });

    describe("jumping to a statement", () => {
        it("opens the document and puts the cursor on it", async () => {
            const { binding } = createBinding();

            await binding.revealStatement({
                uri: "file:///work/report.sql",
                line: 7,
                character: 4,
            });

            expect(shownEditors.at(-1)?.selection.start)
                .toEqual({ line: 7, character: 4 });
            expect(shownEditors.at(-1)?.selection.isEmpty).toBe(true);

            binding.dispose();
        });
    });

    it("drops its listeners and status bar entry when disposed", () => {
        const { binding } = createBinding();

        binding.dispose();

        expect(statusBarItems[0].disposed).toBe(true);
    });
});

describe("readConnectionHeader", () => {
    it("reads the header this extension writes", () => {
        expect(readConnectionHeader(
            buildConnectionHeader("dba@localhost:3310"),
        )).toBe("dba@localhost:3310");
    });

    it("finds the header below other leading comments", () => {
        expect(readConnectionHeader(
            "-- a report\n\n-- MariaDB connection: dba@h\nSELECT 1;",
        )).toBe("dba@h");
    });

    it("ignores case and spacing", () => {
        expect(readConnectionHeader("--   mariadb CONNECTION:   dba@h  "))
            .toBe("dba@h");
    });

    it("ignores a header further down the file", () => {
        // Only the top of a file is a header; a line in the middle is a
        // comment about something else.
        const text = `${"\n".repeat(10)}-- MariaDB connection: dba@h`;

        expect(readConnectionHeader(text)).toBeUndefined();
    });

    it("returns undefined without a header", () => {
        expect(readConnectionHeader("SELECT 1;")).toBeUndefined();
        expect(readConnectionHeader("")).toBeUndefined();
        expect(readConnectionHeader("-- MariaDB connection:"))
            .toBeUndefined();
    });
});
