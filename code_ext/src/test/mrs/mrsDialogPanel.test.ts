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

import type { ISdkExportValues } from "../../mrs/mrsDialogs.js";
import {
    buildMrsDialogHtml,
    MRS_DIALOG_VIEW_TYPE,
    MrsDialogPanel,
    type IMrsDialogHost,
    type IMrsDialogSpec,
} from "../../mrs/mrsDialogPanel.js";
import type {
    MrsHostMessage,
    MrsWebviewMessage,
} from "../../mrs/mrsDialogProtocol.js";
import {
    env,
    errorMessages,
    fileDialogs,
    informationMessages,
    MockWebview,
    resetVscodeMock,
    Uri,
    ViewColumn,
    webviewPanels,
    type MockWebviewPanel,
} from "../mocks/vscode.js";

const extensionUri = Uri.file("/ext");

const VALID: ISdkExportValues = {
    directory: "/sdk",
    serviceUrl: "https://localhost:8443/svc",
    sdkLanguage: "TypeScript",
    addAppBaseClass: "",
    header: "",
};

interface IRecorded {
    host: IMrsDialogHost;
    saved: Array<string | undefined>;
    logs: string[];
    /** The values every save was called with. */
    saves: unknown[];
}

/** A host recording what the panel tells it. */
const createHost = (): IRecorded => {
    const recorded: IRecorded = {
        host: undefined as unknown as IMrsDialogHost,
        saved: [],
        logs: [],
        saves: [],
    };
    recorded.host = {
        onSaved: (message) => { recorded.saved.push(message); },
        log: (message) => { recorded.logs.push(message); },
    };

    return recorded;
};

/**
 * @param recorded Where the spec's saves are recorded.
 * @param overrides What differs from an SDK export dialog.
 *
 * @returns A dialog spec.
 */
const createSpec = (
    recorded: IRecorded,
    overrides: Partial<IMrsDialogSpec> = {},
): IMrsDialogSpec => {
    return {
        dialog: "sdkExport",
        title: "Export MRS SDK Files for /svc",
        values: VALID,
        context: { languages: ["TypeScript"] },
        save: (values) => {
            recorded.saves.push(values);

            return Promise.resolve("Exported.");
        },
        ...overrides,
    };
};

const currentPanel = (): MockWebviewPanel => {
    return webviewPanels[webviewPanels.length - 1]!;
};

const posted = (): MrsHostMessage[] => {
    return currentPanel().webview.posted as MrsHostMessage[];
};

const receive = async (message: MrsWebviewMessage): Promise<void> => {
    currentPanel().webview.receive(message);
    await new Promise((resolve) => { setTimeout(resolve, 0); });
};

describe("buildMrsDialogHtml", () => {
    it("loads the mrs bundle under a nonced policy", () => {
        const html = buildMrsDialogHtml(new MockWebview() as never,
            extensionUri as never, "My Dialog");

        expect(html).toContain("/ext/dist/webview/mrs.js");
        expect(html).toContain("/ext/dist/webview/mrs.css");
        expect(html).not.toContain("sandbox.js");
        expect(html).toContain("default-src 'none'");
        expect(html).toContain("<title>My Dialog</title>");
    });
});

describe("MrsDialogPanel", () => {
    beforeEach(() => {
        MrsDialogPanel.disposeAll();
        resetVscodeMock();
    });

    it("opens a panel and sends the dialog once the page is ready",
        async () => {
            const recorded = createHost();
            const spec = createSpec(recorded);

            MrsDialogPanel.show(extensionUri as never, spec, recorded.host);

            expect(webviewPanels).toHaveLength(1);
            expect(currentPanel().viewType).toBe(MRS_DIALOG_VIEW_TYPE);
            expect(currentPanel().title).toBe("Export MRS SDK Files for /svc");
            expect(currentPanel().viewColumn).toBe(ViewColumn.Active);
            expect(currentPanel().options).toMatchObject({
                enableScripts: true, retainContextWhenHidden: true,
            });
            expect(currentPanel().webview.html)
                .toContain("/ext/dist/webview/mrs.js");
            expect(posted()).toEqual([]);

            await receive({ type: "ready" });

            expect(posted()).toEqual([{
                type: "load",
                dialog: "sdkExport",
                title: "Export MRS SDK Files for /svc",
                values: VALID,
                context: { languages: ["TypeScript"] },
            }]);
        });

    it("refuses invalid values with the first problem, saving nothing",
        async () => {
            const recorded = createHost();
            MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
                recorded.host);

            await receive({
                type: "save",
                values: { ...VALID, directory: "", serviceUrl: "" },
            });

            expect(posted()).toEqual([{
                type: "saveError", message: "Please specify a directory.",
            }]);
            expect(recorded.saves).toEqual([]);
            expect(recorded.saved).toEqual([]);
            expect(currentPanel().disposed).toBe(false);
        });

    it("saves valid values, tells the host and closes", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);
        const panel = currentPanel();

        await receive({ type: "save", values: VALID });

        expect(recorded.saves).toEqual([VALID]);
        expect(recorded.saved).toEqual(["Exported."]);
        expect(panel.disposed).toBe(true);
        // Busy first; nothing is sent to a panel that is gone.
        expect(posted()).toEqual([{ type: "busy", busy: true }]);
    });

    it("is busy while the save runs", async () => {
        const recorded = createHost();
        let finish: (value: string | undefined) => void = () => { /* */ };
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
            save: () => {
                return new Promise((resolve) => { finish = resolve; });
            },
        }), recorded.host);

        await receive({ type: "save", values: VALID });
        expect(posted()).toEqual([{ type: "busy", busy: true }]);
        expect(currentPanel().disposed).toBe(false);

        finish(undefined);
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(recorded.saved).toEqual([undefined]);
        expect(currentPanel().disposed).toBe(true);
    });

    it("keeps the dialog open on a failed save, saying why", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
            save: () => { return Promise.reject(new Error("Access denied")); },
        }), recorded.host);

        await receive({ type: "save", values: VALID });

        expect(posted()).toEqual([
            { type: "busy", busy: true },
            { type: "saveError", message: "Access denied" },
            { type: "busy", busy: false },
        ]);
        expect(recorded.logs).toEqual([
            "Failed to save the Export MRS SDK Files for /svc: Access denied"]);
        expect(recorded.saved).toEqual([]);
        expect(currentPanel().disposed).toBe(false);
        expect(errorMessages).toEqual([]);
    });

    it("shows a failure as a notification once the dialog was closed",
        async () => {
            const recorded = createHost();
            let fail: (error: Error) => void = () => { /* */ };
            MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
                save: () => {
                    return new Promise((_resolve, reject) => {
                        fail = reject;
                    });
                },
            }), recorded.host);
            const panel = currentPanel();

            await receive({ type: "save", values: VALID });
            panel.dispose();
            fail(new Error("Too late"));
            await new Promise((resolve) => { setTimeout(resolve, 0); });

            expect(errorMessages).toEqual(["MariaDB: Too late"]);
            expect(posted()).toEqual([{ type: "busy", busy: true }]);
            expect(recorded.logs).toHaveLength(1);
        });

    it("sends a browsed folder or file to the field asking", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);
        fileDialogs.openAnswer = [Uri.file("/picked")];

        await receive({ type: "browse", field: "directory", folders: true });
        await receive({ type: "browse", field: "iconPath", folders: false });

        expect(fileDialogs.openCalls).toEqual([
            {
                canSelectFolders: true,
                canSelectFiles: false,
                canSelectMany: false,
            },
            {
                canSelectFolders: false,
                canSelectFiles: true,
                canSelectMany: false,
            },
        ]);
        expect(posted()).toEqual([
            { type: "browsed", field: "directory", path: "/picked" },
            { type: "browsed", field: "iconPath", path: "/picked" },
        ]);
    });

    it("sends nothing when browsing is cancelled", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);

        await receive({ type: "browse", field: "directory", folders: true });
        fileDialogs.openAnswer = [];
        await receive({ type: "browse", field: "directory", folders: true });

        expect(posted()).toEqual([]);
    });

    it("loads a referenced table's columns", async () => {
        const recorded = createHost();
        const asked: string[] = [];
        const columns = {
            schema: "sakila", name: "film", type: "TABLE" as const,
        };
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
            loadColumns: (schema, table) => {
                asked.push(`${schema}.${table}`);

                return table === "film"
                    ? Promise.resolve(columns)
                    : Promise.reject(new Error("No such table"));
            },
        }), recorded.host);

        await receive({
            type: "loadColumns", requestId: 1, schema: "sakila", table: "film",
        });
        await receive({
            type: "loadColumns", requestId: 2, schema: "sakila", table: "nope",
        });

        expect(asked).toEqual(["sakila.film", "sakila.nope"]);
        expect(posted()).toEqual([
            { type: "columns", requestId: 1, columns },
            { type: "columns", requestId: 2, error: "No such table" },
        ]);
    });

    it("says columns are not available where the dialog has none",
        async () => {
            const recorded = createHost();
            MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
                recorded.host);

            await receive({
                type: "loadColumns", requestId: 7, schema: "s", table: "t",
            });

            expect(posted()).toEqual([
                { type: "columns", requestId: 7, error: "Not available." }]);
        });

    it("analyzes a folder's MRS scripts", async () => {
        const recorded = createHost();
        const asked: string[] = [];
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
            analyzeFolder: (directory, ignoreList) => {
                asked.push(`${directory}|${ignoreList}`);
                if (directory === "/bad") {
                    return Promise.reject(new Error("Unreadable"));
                }

                return Promise.resolve({
                    language: "TypeScript",
                    definitions: { script_modules: [] },
                });
            },
        }), recorded.host);

        await receive({
            type: "analyzeFolder", directory: "/app", ignoreList: "x",
        });
        await receive({
            type: "analyzeFolder", directory: "/bad", ignoreList: "",
        });
        // An empty folder field is not asked about.
        await receive({ type: "analyzeFolder", directory: "", ignoreList: "" });

        expect(asked).toEqual(["/app|x", "/bad|"]);
        expect(posted()).toEqual([
            {
                type: "scripts",
                directory: "/app",
                language: "TypeScript",
                definitions: { script_modules: [] },
            },
            { type: "scripts", directory: "/bad", error: "Unreadable" },
        ]);
    });

    it("ignores a folder analysis the dialog does not offer", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);

        await receive({
            type: "analyzeFolder", directory: "/a", ignoreList: "",
        });

        expect(posted()).toEqual([]);
    });

    it("copies the SQL preview to the clipboard", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);

        await receive({ type: "copy", text: "CREATE REST SERVICE /a;" });

        expect(env.clipboard.text).toBe("CREATE REST SERVICE /a;");
        expect(informationMessages).toEqual([
            "The REST SQL was copied to the clipboard."]);
    });

    it("closes on cancel", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);

        await receive({ type: "cancel" });

        expect(currentPanel().disposed).toBe(true);
        expect(recorded.saved).toEqual([]);
    });

    it("keeps one panel per dialog, showing the new subject in it",
        async () => {
            const recorded = createHost();
            MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
                recorded.host);
            MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
                title: "Export MRS SDK Files for /other",
                values: { ...VALID, directory: "/other" },
            }), recorded.host);

            expect(webviewPanels).toHaveLength(1);
            expect(currentPanel().title)
                .toBe("Export MRS SDK Files for /other");
            expect(currentPanel().revealed).toBe(1);
            expect(currentPanel().webview.html)
                .toContain("<title>Export MRS SDK Files for /other</title>");

            // The page reloads and asks again; it gets the new dialog.
            await receive({ type: "ready" });
            expect(posted()).toEqual([expect.objectContaining({
                title: "Export MRS SDK Files for /other",
                values: { ...VALID, directory: "/other" },
            })]);
        });

    it("tells the host of the last show about a save", async () => {
        const first = createHost();
        const second = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(first),
            first.host);
        MrsDialogPanel.show(extensionUri as never, createSpec(second),
            second.host);

        await receive({ type: "save", values: VALID });

        // The spec saved is the second's; so should the host told be.
        expect(second.saves).toEqual([VALID]);
        expect(second.saved).toEqual(["Exported."]);
        expect(first.saved).toEqual([]);
    });

    it("opens a panel of its own for another kind of dialog", () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
            dialog: "service", title: "Service",
        }), recorded.host);

        expect(webviewPanels).toHaveLength(2);
    });

    it("opens a new panel once the old one was closed", () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);
        currentPanel().dispose();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);

        expect(webviewPanels).toHaveLength(2);
        expect(currentPanel().disposed).toBe(false);
    });

    it("closes every dialog on disposeAll", () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded, {
            dialog: "user", title: "User",
        }), recorded.host);

        MrsDialogPanel.disposeAll();

        expect(webviewPanels.every((panel) => { return panel.disposed; }))
            .toBe(true);
        // Nothing is left to reuse.
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);
        expect(webviewPanels).toHaveLength(3);
    });

    it("stops listening to a closed panel", async () => {
        const recorded = createHost();
        MrsDialogPanel.show(extensionUri as never, createSpec(recorded),
            recorded.host);
        const panel = currentPanel();
        panel.dispose();

        panel.webview.receive({ type: "ready" });
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(panel.webview.posted).toEqual([]);
    });
});
