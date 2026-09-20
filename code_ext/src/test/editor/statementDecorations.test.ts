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

import { StatementDecorator } from "../../editor/statementDecorations.js";
import {
    decorationTypes,
    fireDocumentChange,
    fireDocumentClose,
    fireVisibleEditorsChange,
    MockTextDocument,
    MockTextEditor,
    resetVscodeMock,
    setVisibleTextEditors,
    Uri,
} from "../mocks/vscode.js";

const extensionUri = Uri.file("/ext") as never;

/**
 * @param text The document's contents.
 * @param languageId Its language.
 *
 * @returns An editor over a document holding that text.
 */
const createEditor = (text: string, languageId = "sql"): MockTextEditor => {
    return new MockTextEditor(new MockTextDocument(
        Uri.file("/work/query.sql"), languageId, text));
};

/**
 * Builds a decorator that yields after every statement, so the
 * progressive behaviour can be observed rather than raced against.
 *
 * @param editors The editors to report as visible.
 *
 * @returns The decorator.
 */
const createDecorator = (
    editors: MockTextEditor[] = [],
): StatementDecorator => {
    setVisibleTextEditors(editors);

    return new StatementDecorator(extensionUri, {
        sliceMs: -1,
        applyIntervalMs: -1,
        debounceMs: 1,
    });
};

describe("StatementDecorator", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("creates one gutter decoration with a light and a dark dot", () => {
        const decorator = createDecorator();

        expect(decorationTypes).toHaveLength(1);
        expect(decorationTypes[0].options).toMatchObject({
            gutterIconSize: "contain",
            light: {
                gutterIconPath: Uri.joinPath(
                    Uri.file("/ext"), "images", "light",
                    "statementStart.svg"),
            },
            dark: {
                gutterIconPath: Uri.joinPath(
                    Uri.file("/ext"), "images", "dark",
                    "statementStart.svg"),
            },
        });

        decorator.dispose();
    });

    it("marks the first line of each statement", async () => {
        const editor = createEditor(
            "SELECT 1;\nSELECT 2;\n\nSELECT 3;");
        const decorator = createDecorator();

        await decorator.decorate(editor as never);

        expect(editor.decoratedLines).toEqual([0, 1, 3]);

        decorator.dispose();
    });

    it("marks a statement at its content, not at its comment", async () => {
        const editor = createEditor(
            "-- a note\n-- another\nSELECT 1;\nSELECT 2;");
        const decorator = createDecorator();

        await decorator.decorate(editor as never);

        // The dot belongs on the SQL, not on the comments above it.
        expect(editor.decoratedLines).toEqual([2, 3]);

        decorator.dispose();
    });

    it("marks nothing in a file of only comments", async () => {
        const editor = createEditor("-- a note\n-- another\n");
        const decorator = createDecorator();

        await decorator.decorate(editor as never);

        expect(editor.decoratedLines).toEqual([]);

        decorator.dispose();
    });

    it("marks a multi line statement once, on its first line", async () => {
        const editor = createEditor(
            "SELECT\n  1,\n  2;\nSELECT 3;");
        const decorator = createDecorator();

        await decorator.decorate(editor as never);

        expect(editor.decoratedLines).toEqual([0, 3]);

        decorator.dispose();
    });

    it("marks a procedure body as one statement", async () => {
        const editor = createEditor([
            "DELIMITER //",
            "CREATE PROCEDURE p()",
            "BEGIN",
            "  SELECT 1;",
            "END//",
            "DELIMITER ;",
            "CALL p();",
        ].join("\n"));
        const decorator = createDecorator();

        await decorator.decorate(editor as never);

        // The DELIMITER lines and the procedure body do not each get a
        // dot; the procedure is one statement, starting on line 1.
        expect(editor.decoratedLines).toEqual([1, 6]);

        decorator.dispose();
    });

    it("shows the first dots before the scan has finished", async () => {
        const lines: string[] = [];
        for (let index = 0; index < 50; index += 1) {
            lines.push(`SELECT ${index};`);
        }
        const editor = createEditor(lines.join("\n"));
        const decorator = createDecorator();

        await decorator.decorate(editor as never);

        // Sliced after every statement, so the dots arrive in batches
        // rather than all at the end.
        expect(editor.decorationCalls.length).toBeGreaterThan(1);
        expect(editor.decorationCalls[0].ranges.length)
            .toBeLessThan(editor.decoratedLines.length);
        expect(editor.decoratedLines).toHaveLength(50);
    });

    it("only ever grows the set of dots while scanning", async () => {
        const lines: string[] = [];
        for (let index = 0; index < 20; index += 1) {
            lines.push(`SELECT ${index};`);
        }
        const editor = createEditor(lines.join("\n"));
        const decorator = createDecorator();

        await decorator.decorate(editor as never);

        const counts = editor.decorationCalls.map((call) => {
            return call.ranges.length;
        });
        expect(counts).toEqual([...counts].sort((a, b) => {
            return a - b;
        }));
        expect(counts.at(-1)).toBe(20);

        decorator.dispose();
    });

    it("abandons a scan when the document changes under it", async () => {
        const editor = createEditor("SELECT 1;\nSELECT 2;\nSELECT 3;");
        const decorator = createDecorator();

        const scan = decorator.decorate(editor as never);
        // The scan yields after every statement, so this lands mid-scan.
        editor.document.setText("SELECT 9;");
        await scan;

        // Whatever it had shown, it did not finish on stale text.
        expect(editor.decoratedLines.length).toBeLessThan(3);

        decorator.dispose();
    });

    it("abandons a scan when a newer one starts", async () => {
        const editor = createEditor("SELECT 1;\nSELECT 2;\nSELECT 3;");
        const decorator = createDecorator();

        const first = decorator.decorate(editor as never);
        const second = decorator.decorate(editor as never);
        await Promise.all([first, second]);

        expect(editor.decoratedLines).toEqual([0, 1, 2]);

        decorator.dispose();
    });

    it("decorates the visible SQL editors when they change", async () => {
        const decorator = createDecorator();
        const editor = createEditor("SELECT 1;\nSELECT 2;");

        fireVisibleEditorsChange([editor]);
        await new Promise((resolve) => {
            setTimeout(resolve, 20);
        });

        expect(editor.decoratedLines).toEqual([0, 1]);

        decorator.dispose();
    });

    it("leaves a non-SQL editor alone", async () => {
        const decorator = createDecorator();
        const editor = createEditor("SELECT 1;", "python");

        fireVisibleEditorsChange([editor]);
        await new Promise((resolve) => {
            setTimeout(resolve, 20);
        });

        expect(editor.decorationCalls).toEqual([]);

        decorator.dispose();
    });

    it("rescans a short while after the document is edited", async () => {
        const editor = createEditor("SELECT 1;");
        const decorator = createDecorator([editor]);
        await new Promise((resolve) => {
            setTimeout(resolve, 20);
        });
        expect(editor.decoratedLines).toEqual([0]);

        editor.document.setText("SELECT 1;\nSELECT 2;\nSELECT 3;");
        fireDocumentChange(editor.document);
        await new Promise((resolve) => {
            setTimeout(resolve, 40);
        });

        expect(editor.decoratedLines).toEqual([0, 1, 2]);

        decorator.dispose();
    });

    it("stops scanning a document that was closed", async () => {
        const editor = createEditor("SELECT 1;\nSELECT 2;\nSELECT 3;");
        const decorator = createDecorator();

        const scan = decorator.decorate(editor as never);
        fireDocumentClose(editor.document);
        await scan;

        expect(editor.decoratedLines.length).toBeLessThan(3);

        decorator.dispose();
    });

    it("drops its decoration type and listeners when disposed", () => {
        const decorator = createDecorator();

        decorator.dispose();

        expect(decorationTypes[0].disposed).toBe(true);
    });

    it("stops a scan in flight when disposed", async () => {
        const editor = createEditor("SELECT 1;\nSELECT 2;\nSELECT 3;");
        const decorator = createDecorator();

        const scan = decorator.decorate(editor as never);
        decorator.dispose();
        await scan;

        expect(editor.decoratedLines.length).toBeLessThan(3);
    });
});
