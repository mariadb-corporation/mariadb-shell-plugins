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
 * Tokenizes SQL with the built grammar exactly as VS Code does: the
 * same `vscode-textmate` on the same Oniguruma, so what a test sees is
 * what the editor colours.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

// Both packages are CommonJS: default imports work under Node's ESM
// loader and under vitest alike, named ones only under vitest.
import oniguruma from "vscode-oniguruma";
import textmate, { type IGrammar } from "vscode-textmate";

const { loadWASM, OnigScanner, OnigString } = oniguruma;
const { INITIAL, Registry } = textmate;

const require = createRequire(import.meta.url);
const syntaxes = join(import.meta.dirname, "..", "..", "..", "syntaxes");

/** The grammar's scope name. */
const SCOPE = "source.mariadb-sql";

/** One token of a line: its text and its innermost scope. */
export interface IToken {
    text: string;
    /** The scopes from the outside in, the grammar's own left out. */
    scopes: string[];
}


const registry = new Registry({
    onigLib: (async () => {
        const wasm = readFileSync(require.resolve(
            "vscode-oniguruma/release/onig.wasm"));
        await loadWASM(wasm.buffer.slice(wasm.byteOffset,
            wasm.byteOffset + wasm.byteLength));

        return {
            createOnigScanner: (patterns: string[]) => {
                return new OnigScanner(patterns);
            },
            createOnigString: (text: string) => {
                return new OnigString(text);
            },
        };
    })(),
    loadGrammar: (scopeName: string) => {
        if (scopeName !== SCOPE) {
            return Promise.resolve(null);
        }

        return Promise.resolve(JSON.parse(readFileSync(
            join(syntaxes, "mariadb-sql.tmLanguage.json"), "utf8")));
    },
});

let loading: Promise<IGrammar> | undefined;

/**
 * Loads the grammar, once.
 *
 * @returns The grammar.
 */
const grammar = (): Promise<IGrammar> => {
    loading ??= registry.loadGrammar(SCOPE).then((loaded) => {
        if (!loaded) {
            throw new Error(`no grammar for ${SCOPE}`);
        }

        return loaded;
    });

    return loading;
};

/**
 * Tokenizes text, line by line with the state carried across lines as
 * the editor does.
 *
 * @param text The SQL.
 *
 * @returns The tokens of each line.
 */
export const tokenize = async (text: string): Promise<IToken[][]> => {
    const loaded = await grammar();
    const lines: IToken[][] = [];
    let state = INITIAL;
    for (const line of text.split("\n")) {
        const result = loaded.tokenizeLine(line, state);
        lines.push(result.tokens.map((token) => {
            return {
                text: line.slice(token.startIndex, token.endIndex),
                scopes: token.scopes.slice(1),
            };
        }));
        state = result.ruleStack;
    }

    return lines;
};

/**
 * The innermost scope of each token of one line, for assertions that
 * read like the line: `["SELECT", "keyword.other.reserved.sql"]`.
 *
 * @param line The SQL, one line.
 *
 * @returns Token text and innermost scope, whitespace tokens left out.
 */
export const scopesOf = async (
    line: string,
): Promise<Array<[string, string]>> => {
    const [tokens] = await tokenize(line);

    return tokens.filter((token) => {
        return token.text.trim() !== "";
    }).map((token) => {
        return [token.text, token.scopes.at(-1) ?? ""];
    });
};
