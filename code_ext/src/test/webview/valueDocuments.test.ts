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

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
    extensionOfValue,
    ValueDocuments,
    VALUE_SCHEME,
} from "../../webview/valueDocuments.js";
import { FilePermission, resetVscodeMock } from "../mocks/vscode.js";

const encode = (text: string): Uint8Array => {
    return new TextEncoder().encode(text);
};

describe("extensionOfValue", () => {
    it("opens a JSON column as JSON", () => {
        expect(extensionOfValue("json", encode("[1"), "[1")).toBe("json");
    });

    it("opens text as JSON or XML where it is, and as text otherwise", () => {
        expect(extensionOfValue("text", encode(""), " {\"a\": 1} ")).toBe("json");
        expect(extensionOfValue("text", encode(""), "{not json")).toBe("txt");
        expect(extensionOfValue("text", encode(""), "<?xml version=\"1.0\"?>"))
            .toBe("xml");
        expect(extensionOfValue("text", encode(""), "<root/>")).toBe("xml");
        expect(extensionOfValue("text", encode(""), "3 < 4")).toBe("txt");
    });

    it("opens a binary value as the file its content is", () => {
        const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);

        expect(extensionOfValue("binary", png, "")).toBe("png");
        expect(extensionOfValue("binary", encode("<svg></svg>"), ""))
            .toBe("svg");
    });
});

describe("ValueDocuments", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("serves a value as a file, named after its cell", () => {
        const documents = new ValueDocuments();

        const uri = documents.put("run1-result-0/2/info", {
            value: "{\"a\": 1}",
            kind: "json",
            name: "items-info-3",
            readOnly: false,
            writeBack: vi.fn(),
        });

        expect(uri.scheme).toBe(VALUE_SCHEME);
        expect(uri.path).toBe(
            "/run1-result-0%2F2%2Finfo/items-info-3.json");
        expect(new TextDecoder().decode(documents.readFile(uri)))
            .toBe("{\"a\": 1}");
        expect(documents.stat(uri).permissions).toBeUndefined();
    });

    it("serves a binary value as the bytes its hex stands for", () => {
        const documents = new ValueDocuments();

        const uri = documents.put("k", {
            value: "89504e47", kind: "binary", name: "image",
            readOnly: true, writeBack: vi.fn(),
        });

        expect(uri.path.endsWith("/image.png")).toBe(true);
        expect([...documents.readFile(uri)]).toEqual([0x89, 0x50, 0x4e, 0x47]);
        expect(documents.stat(uri).permissions).toBe(FilePermission.Readonly);
    });

    it("writes a save back to its cell, as the grid holds it", async () => {
        const documents = new ValueDocuments();
        const writeBack = vi.fn(() => { return Promise.resolve(); });
        const text = documents.put("t", {
            value: "old", kind: "text", name: "t", readOnly: false, writeBack,
        });
        const binary = documents.put("b", {
            value: "00", kind: "binary", name: "b", readOnly: false, writeBack,
        });

        await documents.writeFile(text, encode("new\nlines"));
        await documents.writeFile(binary, new Uint8Array([1, 255]));

        expect(writeBack.mock.calls).toEqual([["new\nlines"], ["01ff"]]);
        expect(new TextDecoder().decode(documents.readFile(text)))
            .toBe("new\nlines");
    });

    it("refuses to save a read-only value", async () => {
        const documents = new ValueDocuments();
        const uri = documents.put("r", {
            value: "x", kind: "text", name: "r", readOnly: true,
            writeBack: vi.fn(),
        });

        await expect(documents.writeFile(uri, encode("y")))
            .rejects.toMatchObject({ code: "NoPermissions" });
    });

    it("fails a save whose cell is gone, and keeps what it had", async () => {
        const documents = new ValueDocuments();
        const uri = documents.put("g", {
            value: "x", kind: "text", name: "g", readOnly: false,
            writeBack: () => {
                return Promise.reject(new Error("The page is gone."));
            },
        });

        await expect(documents.writeFile(uri, encode("y")))
            .rejects.toMatchObject({
                code: "Unavailable", message: "The page is gone.",
            });
        expect(new TextDecoder().decode(documents.readFile(uri))).toBe("x");
    });

    it("tells an open editor when its cell is opened again", () => {
        const documents = new ValueDocuments();
        const changes: unknown[] = [];
        documents.onDidChangeFile((events) => { changes.push(...events); });
        const toOpen = {
            value: "a", kind: "text" as const, name: "n", readOnly: false,
            writeBack: vi.fn(),
        };

        documents.put("same", toOpen);
        expect(changes).toHaveLength(0);
        documents.put("same", { ...toOpen, value: "b" });

        expect(changes).toHaveLength(1);
    });

    it("forgets a value once its editor is closed", () => {
        const documents = new ValueDocuments();
        const uri = documents.put("f", {
            value: "a", kind: "text", name: "f", readOnly: false,
            writeBack: vi.fn(),
        });

        documents.forget(uri);

        expect(documents.size).toBe(0);
        expect(() => { documents.readFile(uri); })
            .toThrow(/Not found/);
    });
});
