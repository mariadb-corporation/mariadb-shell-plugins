/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { describe, expect, it } from "vitest";

import { LineReader } from "./lineReader.js";

describe("LineReader", () => {
    it("emits whole lines", () => {
        const lines: string[] = [];
        const reader = new LineReader((line) => {
            lines.push(line);
        });

        reader.push("==> Downloading\n==> Verifying checksum\n");

        expect(lines).toEqual(["==> Downloading", "==> Verifying checksum"]);
    });

    it("joins a line that arrives in two chunks", () => {
        const lines: string[] = [];
        const reader = new LineReader((line) => {
            lines.push(line);
        });

        reader.push("==> Unpack");
        expect(lines).toEqual([]);

        reader.push("ing into /opt\n");
        expect(lines).toEqual(["==> Unpacking into /opt"]);
    });

    it("strips carriage returns", () => {
        const lines: string[] = [];
        const reader = new LineReader((line) => {
            lines.push(line);
        });

        reader.push("==> Downloading\r\n");

        expect(lines).toEqual(["==> Downloading"]);
    });

    it("skips blank lines", () => {
        const lines: string[] = [];
        const reader = new LineReader((line) => {
            lines.push(line);
        });

        reader.push("\n\n==> Done\n");

        expect(lines).toEqual(["==> Done"]);
    });

    it("flushes a trailing line without a newline", () => {
        const lines: string[] = [];
        const reader = new LineReader((line) => {
            lines.push(line);
        });

        reader.push("==> Installed 26.9.2");
        reader.flush();
        reader.flush();

        expect(lines).toEqual(["==> Installed 26.9.2"]);
    });
});
