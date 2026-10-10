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

import * as assert from "node:assert/strict";

import {
    BottomBarPanel,
    By,
    CustomTreeSection,
    EditorView,
    Key,
    TextEditor,
    WebView,
    WebviewView,
} from "vscode-extension-tester";

import {
    CONNECTION,
    driver,
    inlineAction,
    menuAction,
    openSection,
    runCommand,
    screenshot,
    SERVER_TIMEOUT,
    sleep,
    treeItem,
    waitFor,
} from "../lib/ui";

/**
 * Running SQL from an editor and what the result view makes of it: a
 * statement's rows in the grid, a script's error in the error bar, Stop on
 * Error, a table's rows opened with Select Rows in a tab of their own, and
 * a cell edited there and written back.
 */

let connections: CustomTreeSection;

/** Works inside the result view in the bottom panel. */
const inResults = async <T>(work: (view: WebviewView) => Promise<T>)
    : Promise<T> => {
    const panel = new BottomBarPanel();
    await panel.toggle(true);
    await panel.openTab("MariaDB");
    const view = new WebviewView();
    await view.switchToFrame();
    try {
        return await work(view);
    } finally {
        await view.switchBack();
    }
};

/** The texts of a grid's cells, in the webview that is switched to. */
const cellTexts = async (view: WebView | WebviewView): Promise<string[]> => {
    const cells = await view.findWebElements(By.css(".tabulator-cell"));

    return Promise.all(cells.map(async (cell) => {
        return (await cell.getText()).trim();
    }));
};

/** Opens a new SQL editor on the sandbox and puts the text in it. */
const sqlEditor = async (text: string): Promise<TextEditor> => {
    await inlineAction(await treeItem(connections, CONNECTION),
        "New SQL Editor");
    const editor = await waitFor(async () => {
        const title = await (await new EditorView().getActiveTab())?.getTitle();

        return title?.includes("MariaDB connection") === true
            ? new TextEditor() : undefined;
    }, "the new SQL editor");
    const header = await editor.getText();
    await editor.setText(`${header.trimEnd()}\n${text}\n`);

    return editor;
};

/** Closes the SQL editors without saving them. */
const discardEditor = async (): Promise<void> => {
    const editors = new EditorView();
    for (const title of await editors.getOpenEditorTitles()) {
        if (title.includes("MariaDB connection")) {
            // Brought to the front first: focus may be in a webview, where
            // the command palette's commands do not reach.
            await editors.openEditor(title);
            await runCommand("View: Revert and Close Editor");
        }
    }
};

describe("SQL editors and results", () => {
    before(async () => {
        connections = await openSection("Connections");
    });

    it("shows a statement's rows in the result view", async () => {
        const editor = await sqlEditor(
            "SELECT id, name FROM uitest.city ORDER BY id;");
        await editor.moveCursor(2, 5);
        await runCommand("MariaDB: Run SQL Statement at Cursor");
        await inResults(async (view) => {
            const cells = await waitFor(async () => {
                const texts = await cellTexts(view);

                return texts.includes("Berlin") ? texts : undefined;
            }, "the rows in the grid", SERVER_TIMEOUT);
            assert.ok(cells.includes("Vienna"));
            await screenshot("04-result-grid");
        });
        await discardEditor();
    });

    it("shows a failed statement in the error bar", async () => {
        await sqlEditor("SELECT 1;\nSELECT * FROM uitest.nope;\nSELECT 2;");
        await runCommand("MariaDB: Run SQL Script");
        await inResults(async (view) => {
            const text = await waitFor(async () => {
                const bars = await view.findWebElements(
                    By.css(".errorBarText"));

                return bars.length > 0 ? await bars[0].getText() : undefined;
            }, "the error bar", SERVER_TIMEOUT);
            assert.match(text, /nope/);
        });
        await discardEditor();
    });

    it("switches Stop on Error off and on again", async () => {
        await sqlEditor("SELECT 1;");
        await runCommand("MariaDB: Stop on Error (on)");
        await sleep(500);
        await runCommand("MariaDB: Stop on Error (off)");
        await discardEditor();
    });

    it("opens a table's rows with Select Rows, in a tab of their own",
        async () => {
            await menuAction(connections,
                [...CONNECTION, "uitest", "Tables", "city"], ["Select Rows"],
                async () => {
                    return (await new EditorView().getOpenEditorTitles())
                        .some((title) => { return title.includes("uitest.city"); });
                }, "the uitest.city tab");
            const title = (await new EditorView().getOpenEditorTitles())
                .find((candidate) => { return candidate.includes("uitest.city"); })!;
            await new EditorView().openEditor(title);
            const view = new WebView();
            await view.switchToFrame();
            try {
                const cells = await waitFor(async () => {
                    const texts = await cellTexts(view);

                    return texts.includes("Vienna") ? texts : undefined;
                }, "the table's rows", SERVER_TIMEOUT);
                assert.ok(cells.includes("Berlin"));

                // Edited in place and written back.
                // Start Editing opens the first editable cell.
                await (await view.findWebElement(
                    By.css("[aria-label='Start Editing']"))).click();
                await waitFor(async () => {
                    return (await view.findWebElements(By.css(
                        ".tabulator-cell input"))).length > 0;
                }, "the first cell's editor");
                await driver().switchTo().activeElement().sendKeys(Key.ESCAPE);
                const berlin = await waitFor(async () => {
                    for (const cell of await view.findWebElements(
                        By.css(".tabulator-cell"))) {
                        if ((await cell.getText()).trim() === "Berlin") {
                            return cell;
                        }
                    }

                    return undefined;
                }, "the Berlin cell");
                // A click opens a cell's editor.
                await berlin.click();
                // Typed into what has the focus: Tabulator builds its
                // editor anew right after the double click, so an element
                // found before that would be stale.
                await waitFor(async () => {
                    const inputs = await view.findWebElements(
                        By.css(".tabulator-cell input, .tabulator-cell textarea"));

                    return inputs.length > 0;
                }, "the cell editor");
                await sleep(300);
                const selectAll = process.platform === "darwin"
                    ? Key.COMMAND : Key.CONTROL;
                await driver().switchTo().activeElement().sendKeys(
                    Key.chord(selectAll, "a"), "Munich", Key.ENTER);
                await (await view.findWebElement(
                    By.css("[aria-label='Apply Changes']"))).click();
                await sleep(1000);
                await (await view.findWebElement(
                    By.css("[aria-label='Refresh']"))).click();
                await waitFor(async () => {
                    return (await cellTexts(view)).includes("Munich");
                }, "the edited value after a refresh", SERVER_TIMEOUT);
                await screenshot("04-edited");
            } finally {
                await view.switchBack();
            }
            await new EditorView().closeEditor(title);
        });
});
