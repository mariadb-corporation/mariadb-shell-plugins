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
 * Real SQL, tokenized whole and compared with a snapshot: a change to a
 * rule shows up as a diff of the tokens it moved, in review. And an
 * invariant over every token of every file: nothing but whitespace is
 * left without a scope, so a character the grammar has no rule for
 * cannot slip through uncoloured.
 *
 * The corpora: the sakila schema, and the shell's REST SQL grammar test
 * (`unittest/data/mrs/grammar_test.sql`), copied here so that the test
 * needs no checkout beside this one. Refresh them from there as they
 * grow.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { tokenize } from "./tokenize.js";

const corpus = join(import.meta.dirname, "corpus");

const files = readdirSync(corpus).filter((name) => {
    return name.endsWith(".sql");
});

describe.each(files)("%s", (file) => {
    const text = readFileSync(join(corpus, file), "utf8");
    const sourceLines = text.split("\n");
    // Tokenized once, for both tests - when the first one asks.
    let tokenized: ReturnType<typeof tokenize> | undefined;
    const tokens = (): ReturnType<typeof tokenize> => {
        tokenized ??= tokenize(text);

        return tokenized;
    };

    it("tokenizes as it did", async () => {
        const lines = await tokens();
        // One line of the file, then one line per token under it, so a
        // diff shows the SQL the moved token is in.
        const rendered = lines.map((tokens, index) => {
            const source = sourceLines[index];
            const shown = tokens.filter((token) => {
                return token.text.trim() !== "";
            }).map((token) => {
                return `    ${JSON.stringify(token.text)}  `
                    + `${token.scopes.at(-1) ?? "-"}`;
            });

            return [`${index + 1}: ${source}`, ...shown].join("\n");
        }).join("\n");
        await expect(rendered).toMatchFileSnapshot(
            join(corpus, `${file}.tokens`));
    });

    it("leaves no token without a scope", async () => {
        const unscoped: string[] = [];
        for (const [index, line] of (await tokens()).entries()) {
            for (const token of line) {
                if (token.text.trim() !== "" && token.scopes.length === 0) {
                    unscoped.push(`${index + 1}: ${JSON.stringify(token.text)}`);
                }
            }
        }
        expect(unscoped).toEqual([]);
    });
});
