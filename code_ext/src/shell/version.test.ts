/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { describe, expect, it } from "vitest";

import {
    compareVersions,
    formatVersion,
    meetsMinimum,
    parseShellVersionOutput,
    parseVersion,
} from "./version.js";

describe("parseVersion", () => {
    it("reads a full version", () => {
        expect(parseVersion("26.9.2")).toEqual({
            major: 26,
            minor: 9,
            patch: 2,
        });
    });

    it("treats a missing patch level as zero", () => {
        expect(parseVersion("26.9")).toEqual({
            major: 26,
            minor: 9,
            patch: 0,
        });
    });

    it("ignores a leading v", () => {
        expect(parseVersion("v26.9.2")).toEqual({
            major: 26,
            minor: 9,
            patch: 2,
        });
    });

    it("returns undefined without a version", () => {
        expect(parseVersion("current")).toBeUndefined();
        expect(parseVersion("")).toBeUndefined();
    });
});

describe("parseShellVersionOutput", () => {
    it("reads the shell's own version, not the server's", () => {
        const output = "mariadb-shell   Ver 26.9.2 for osx10.21 on arm64 "
            + "- for MariaDB 13.1.0-MariaDB (Source distribution)";

        expect(parseShellVersionOutput(output)).toEqual({
            major: 26,
            minor: 9,
            patch: 2,
        });
    });

    it("reads a Linux build line", () => {
        const output = "mariadb-shell   Ver 27.1.0 for Linux on x86_64 "
            + "- for MariaDB 13.1.0-MariaDB (Source distribution)\n";

        expect(parseShellVersionOutput(output)).toEqual({
            major: 27,
            minor: 1,
            patch: 0,
        });
    });

    it("falls back to the first version when Ver is absent", () => {
        expect(parseShellVersionOutput("mariadb-shell 26.9.3")).toEqual({
            major: 26,
            minor: 9,
            patch: 3,
        });
    });

    it("returns undefined for unrelated output", () => {
        expect(
            parseShellVersionOutput("command not found"),
        ).toBeUndefined();
    });
});

describe("compareVersions", () => {
    it("orders by major, then minor, then patch", () => {
        const a = parseVersion("26.9.2")!;
        const b = parseVersion("26.10.0")!;
        const c = parseVersion("27.0.0")!;

        expect(compareVersions(a, b)).toBeLessThan(0);
        expect(compareVersions(b, c)).toBeLessThan(0);
        expect(compareVersions(c, a)).toBeGreaterThan(0);
        expect(compareVersions(a, { ...a })).toBe(0);
    });
});

describe("meetsMinimum", () => {
    const minimum = parseVersion("26.9.2")!;

    it.each([
        ["26.9.2", true],
        ["26.9.3", true],
        ["26.10.0", true],
        ["27.0.0", true],
        ["26.9.1", false],
        ["26.8.0", false],
        ["25.99.99", false],
    ] as const)("%s meets 26.9.2: %s", (candidate, expected) => {
        expect(meetsMinimum(parseVersion(candidate)!, minimum))
            .toBe(expected);
    });
});

describe("formatVersion", () => {
    it("round trips a parsed version", () => {
        expect(formatVersion(parseVersion("26.9")!)).toBe("26.9.0");
    });
});
