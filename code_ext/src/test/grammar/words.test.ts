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

/**
 * Every word of `syntaxes/words.json` gets the scope its list says. The lists are what `tools/grammar.mts sync` read out of
 * the server and the shell, so this is the proof that no keyword,
 * function or annotation of either is left uncoloured - and that none
 * is swallowed by a rule meant for something else.
 */

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { scopesOf } from "./tokenize.js";

const root = join(import.meta.dirname, "..", "..", "..");

interface IWords {
    sources: Record<string, { path: string; commit: string }>;
    reserved: string[];
    unreserved: string[];
    functions: string[];
    restKeywords: string[];
    restAnnotations: string[];
}

const words = JSON.parse(readFileSync(
    join(root, "syntaxes", "words.json"), "utf8")) as IWords;

/** Keywords the grammar colours as constants instead, on purpose. */
const constants = new Set(["NULL", "TRUE", "FALSE", "UNKNOWN"]);

/** The scope a keyword of a list is expected in. */
const keywordScope = (word: string, list: string): RegExp => {
    return constants.has(word)
        ? /^constant\.language\.sql$/
        : new RegExp(`^keyword\\.other\\.${list}\\.sql$`);
};

/** The innermost scope of a word in a line, on its own. */
const scopeOf = async (
    line: string,
    word: string,
): Promise<string | undefined> => {
    return (await scopesOf(line)).find(([text]) => {
        return text === word;
    })?.[1];
};

describe("the word lists", () => {
    it("record where they came from", () => {
        expect(words.sources.server.commit).not.toBe("");
        expect(words.sources.shell.commit).not.toBe("");
    });

    it("are not empty", () => {
        // Rough floors: a sync that read the wrong file would fall under.
        expect(words.reserved.length).toBeGreaterThan(200);
        expect(words.unreserved.length).toBeGreaterThan(300);
        expect(words.functions.length).toBeGreaterThan(300);
        expect(words.restKeywords.length).toBeGreaterThan(100);
        expect(words.restAnnotations.length).toBeGreaterThan(10);
    });

    it("are built into the grammar", () => {
        // The template and the lists are the source; the grammar VS Code
        // loads is checked in, so an edit without a rebuild fails here.
        expect(() => {
            execFileSync(process.execPath,
                [join(root, "tools", "grammar.mts"), "build", "--check"],
                { stdio: "pipe" });
        }).not.toThrow();
    });
});

describe("the grammar", () => {
    it("colours every reserved keyword", async () => {
        const missed: string[] = [];
        for (const word of words.reserved) {
            for (const form of [word, word.toLowerCase()]) {
                if (!keywordScope(word, "reserved").test(
                    await scopeOf(`x ${form} y`, form) ?? "")) {
                    missed.push(form);
                }
            }
        }
        expect(missed).toEqual([]);
    });

    it("colours every non-reserved keyword", async () => {
        const missed: string[] = [];
        for (const word of words.unreserved) {
            if (!keywordScope(word, "unreserved").test(
                await scopeOf(`x ${word} y`, word) ?? "")) {
                missed.push(word);
            }
        }
        expect(missed).toEqual([]);
    });

    it("colours every function before a parenthesis, and only there",
        async () => {
            const missed: string[] = [];
            for (const word of words.functions) {
                if (await scopeOf(`x ${word}(1)`, word)
                    !== "support.function.sql") {
                    missed.push(word);
                }
                // Without the parenthesis it is a keyword or a name.
                if (await scopeOf(`x ${word} y`, word)
                    === "support.function.sql") {
                    missed.push(`${word} without (`);
                }
            }
            expect(missed).toEqual([]);
        });

    it("colours every REST keyword", async () => {
        const server = new Set([...words.reserved, ...words.unreserved]);
        const missed: string[] = [];
        for (const word of words.restKeywords) {
            // A REST keyword the server knows too is the server's.
            const expected = server.has(word)
                ? keywordScope(word, "(reserved|unreserved)")
                : /^keyword\.other\.rest\.sql$/;
            if (!expected.test(await scopeOf(`x ${word} y`, word)
                ?? "")) {
                missed.push(word);
            }
        }
        expect(missed).toEqual([]);
    });

    it("colours every REST annotation", async () => {
        const missed: string[] = [];
        for (const word of words.restAnnotations) {
            if (await scopeOf(`x @${word} y`, `@${word}`)
                !== "storage.modifier.annotation.rest.sql") {
                missed.push(word);
            }
        }
        expect(missed).toEqual([]);
    });

    it("leaves a word that only starts with a keyword alone", async () => {
        for (const word of ["SELECTION", "SELECT$1", "SELECT_X", "xSELECT",
            "fromage", "1FROM"]) {
            expect(await scopeOf(`x ${word} y`, word), word)
                .toBe("entity.name.identifier.sql");
        }
    });
});
