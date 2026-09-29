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

import {
    bytesOf,
    extensionFor,
    hexOf,
    loadValueFromFile,
    saveValueToFile,
} from "../../webview/valueFiles.js";
import {
    fileDialogs,
    files,
    resetVscodeMock,
    Uri,
} from "../mocks/vscode.js";

const PNG = "89504e470d0a1a0a0000000d49484452";
const SVG = new TextEncoder().encode(
    "<?xml version=\"1.0\"?>\n<!DOCTYPE svg PUBLIC \"-//W3C//DTD SVG 1.1//EN\""
    + " \"x.dtd\">\n<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>");

describe("extensionFor", () => {
    it("knows a file by what it starts with", () => {
        expect(extensionFor(bytesOf(PNG))).toBe("png");
        expect(extensionFor(bytesOf("ffd8ffe000104a46"))).toBe("jpg");
        expect(extensionFor(bytesOf("474946383961"))).toBe("gif");
        expect(extensionFor(bytesOf("255044462d312e34"))).toBe("pdf");
        expect(extensionFor(bytesOf("504b0304"))).toBe("zip");
        expect(extensionFor(new TextEncoder().encode(
            "RIFF\u0000\u0000\u0000\u0000WEBPVP8 "))).toBe("webp");
    });

    it("knows an SVG, with or without its XML declaration", () => {
        expect(extensionFor(SVG)).toBe("svg");
        expect(extensionFor(new TextEncoder().encode("<svg viewBox=\"0 0 1 1\">")))
            .toBe("svg");
    });

    it("calls other text a text file, and anything else binary", () => {
        expect(extensionFor(new TextEncoder().encode("row 1 row 1\n")))
            .toBe("txt");
        expect(extensionFor(bytesOf("00ff13"))).toBe("bin");
        expect(extensionFor(new Uint8Array())).toBe("bin");
    });
});

describe("bytesOf and hexOf", () => {
    it("reads the hex a binary value is held as, 0x or not", () => {
        expect([...bytesOf("00ff")]).toEqual([0, 255]);
        expect([...bytesOf("0x00FF")]).toEqual([0, 255]);
        expect(hexOf(new Uint8Array([0, 255, 16]))).toBe("00ff10");
    });

    it("takes a value that is not hex as its text", () => {
        expect(new TextDecoder().decode(bytesOf("héllo"))).toBe("héllo");
        // An odd number of digits is not a byte string either.
        expect([...bytesOf("abc")]).toEqual([97, 98, 99]);
    });
});

describe("saving and loading a value", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("writes the bytes the hex stands for, and offers their type", async () => {
        fileDialogs.saveAnswer = Uri.file("/tmp/picture.png");

        const saved = await saveValueToFile(PNG, "items-image-3");

        expect(saved?.fsPath).toBe("/tmp/picture.png");
        expect(hexOf(files.get("/tmp/picture.png")!)).toBe(PNG);
        expect(fileDialogs.saveCalls[0]).toMatchObject({
            filters: { "PNG file": ["png"] },
        });
    });

    it("writes nothing when the dialog is cancelled", async () => {
        expect(await saveValueToFile(PNG, "x")).toBeUndefined();
        expect(files.size).toBe(0);
    });

    it("reads a picked file as hex", async () => {
        files.set("/tmp/logo.svg", SVG);
        fileDialogs.openAnswer = [Uri.file("/tmp/logo.svg")];

        expect(await loadValueFromFile()).toBe(hexOf(SVG));
        expect(fileDialogs.openCalls[0]).toMatchObject({
            canSelectMany: false,
        });
    });

    it("reads nothing when no file is picked", async () => {
        expect(await loadValueFromFile()).toBeUndefined();
    });
});
