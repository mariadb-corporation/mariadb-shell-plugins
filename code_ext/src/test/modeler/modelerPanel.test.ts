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

import type { ISchemaModel } from "../../modeler/modelFormat.js";
import {
    MODELER_VIEW_TYPE,
    ModelerPanel,
    type IModelerHost,
    type IModelerTarget,
} from "../../modeler/modelerPanel.js";
import type { ModelerHostMessage } from "../../modeler/modelerProtocol.js";
import { DEFAULT_PAGE_SETTINGS } from "../../modeler/pageSettings.js";
import {
    MockWebview,
    resetVscodeMock,
    Uri,
    webviewPanels,
    type MockWebviewPanel,
} from "../mocks/vscode.js";

const extensionUri = Uri.file("/ext");

const model: ISchemaModel = {
    schema: "world",
    tables: [],
    diagrams: [],
};

const target = (diagram: string, schema = "world"): IModelerTarget => {
    return { connection: "dba@localhost:3310", schema, diagram };
};

const flush = async (): Promise<void> => {
    await new Promise((resolve) => { setTimeout(resolve, 0); });
};

const lastPanel = (): MockWebviewPanel => {
    return webviewPanels[webviewPanels.length - 1];
};

const posted = (panel = lastPanel()): ModelerHostMessage[] => {
    return (panel.webview as MockWebview).posted as ModelerHostMessage[];
};

const host = (logs: string[] = []): IModelerHost => {
    return {
        readModel: () => {
            return Promise.resolve({ model, source: "world.msm_schema_diagrams" });
        },
        log: (message) => { logs.push(message); },
    };
};

describe("ModelerPanel", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("opens on a diagram and sends the model once the page is ready",
        async () => {
            const logs: string[] = [];
            ModelerPanel.show(extensionUri as never, target("MRS"), host(logs));

            const panel = lastPanel();
            expect(panel.viewType).toBe(MODELER_VIEW_TYPE);
            expect(panel.title).toBe("MRS (world)");
            expect(panel.webview.html).toContain("modeler.js");
            expect(posted()).toEqual([]);

            (panel.webview as MockWebview).receive({ type: "ready" });
            await flush();

            expect(posted()).toEqual([{
                type: "load",
                model,
                source: "world.msm_schema_diagrams",
                pageSettings: DEFAULT_PAGE_SETTINGS,
                diagram: "MRS",
            }]);
            expect(logs[0]).toContain("Showing the diagrams of world");

            panel.dispose();
        });

    it("tells the page when the model cannot be read", async () => {
        ModelerPanel.show(extensionUri as never, target("MRS"), {
            readModel: () => { return Promise.reject(new Error("gone")); },
            log: () => { /* nothing */ },
        });
        const panel = lastPanel();
        (panel.webview as MockWebview).receive({ type: "ready" });
        await flush();

        expect(posted()).toEqual([{ type: "error", message: "gone" }]);

        panel.dispose();
    });

    it("shows another diagram of the schema in the panel that is up",
        () => {
            ModelerPanel.show(extensionUri as never, target("MRS"), host());
            const count = webviewPanels.length;
            const panel = lastPanel();

            ModelerPanel.show(extensionUri as never, target("MRS Status"),
                host());

            expect(webviewPanels).toHaveLength(count);
            expect(panel.revealed).toBe(1);
            expect(panel.title).toBe("MRS Status (world)");
            expect(posted(panel)).toEqual([
                { type: "showDiagram", name: "MRS Status" },
            ]);

            panel.dispose();
        });

    it("opens a panel of its own for another schema, and again once closed",
        () => {
            ModelerPanel.show(extensionUri as never, target("MRS"), host());
            const first = lastPanel();
            ModelerPanel.show(extensionUri as never, target("Main", "shop"),
                host());
            const second = lastPanel();

            expect(second).not.toBe(first);
            first.dispose();
            ModelerPanel.show(extensionUri as never, target("MRS"), host());
            expect(lastPanel()).not.toBe(first);

            second.dispose();
            lastPanel().dispose();
        });

    it("escapes a diagram's name in the page, where the database put markup",
        () => {
            ModelerPanel.show(extensionUri as never,
                target("x</title><meta http-equiv=\"refresh\">"), host());
            const panel = lastPanel();

            expect(panel.webview.html).not.toContain(
                "<meta http-equiv=\"refresh\"");
            expect(panel.webview.html).toContain(
                "<title>x&lt;/title&gt;&lt;meta http-equiv=&quot;refresh"
                + "&quot;&gt; (world)</title>");
            panel.dispose();
        });

    it("titles itself by the diagram the page shows", () => {
        ModelerPanel.show(extensionUri as never, target("MRS"), host());
        const panel = lastPanel();

        (panel.webview as MockWebview).receive({
            type: "diagramShown", name: "MRS Status",
        });

        expect(panel.title).toBe("MRS Status (world)");
        panel.dispose();
    });
});
