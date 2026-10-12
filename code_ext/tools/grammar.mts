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
 * Keeps the MariaDB SQL TextMate grammars in step with the servers they
 * highlight for.
 *
 * A TextMate grammar is a tokenizer, so only two things of the MariaDB
 * Server and REST SQL grammars matter to it: the token classes their
 * lexers know (strings, comments, numbers, variables - stable for years,
 * written by hand in `syntaxes/mariadb-sql.template.json`) and the word
 * lists (keywords, functions, annotations - which grow with every
 * release). This tool owns the word lists, so that a change in either
 * source is a regeneration of those lists and nothing else:
 *
 *   node tools/grammar.mts sync --server ~/git/mariadb-server \
 *                              --shell ~/git/mariadb-shell
 *
 * reads them out of the sources into `syntaxes/words.json`, recording
 * which commit of each they came from, and
 *
 *   node tools/grammar.mts build [--check]
 *
 * fills the template's placeholders with them and writes the grammar.
 * `--check` only compares, for CI to fail when the grammar was not
 * rebuilt after an edit.
 *
 * Run with Node 24, which strips the types itself; `.mts` says it is a module.
 */

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const syntaxes = join(dirname(fileURLToPath(import.meta.url)), "..",
    "syntaxes");
const wordsFile = join(syntaxes, "words.json");
const templateFile = join(syntaxes, "mariadb-sql.template.json");

/** Where one list came from, for the record in `words.json`. */
interface ISource {
    /** The checkout the lists were read from. */
    path: string;
    /** Its commit, or what `git describe` made of it. */
    commit: string;
}

/** The word lists, as `words.json` holds them. */
interface IWords {
    sources: { server: ISource; shell: ISource };
    /** Server keywords the parser never accepts as a plain identifier. */
    reserved: string[];
    /** Server keywords that can also name a column, a table, a label. */
    unreserved: string[];
    /**
     * Function names: the ones with a syntax of their own in the parser
     * and the native functions. Only a function when followed by `(`.
     */
    functions: string[];
    /** REST SQL keywords, including those the server knows too. */
    restKeywords: string[];
    /** REST SQL data mapping annotations, without their `@`. */
    restAnnotations: string[];
}

/** The grammar built from the template. */
const grammarFile = "mariadb-sql.tmLanguage.json";

/**
 * Cuts the lines of one C array out of a source file.
 *
 * @param text The file.
 * @param opening What the array's first line contains.
 *
 * @returns The lines between the opening and the closing `};`.
 */
const arrayBody = (text: string, opening: string): string[] => {
    const lines = text.split("\n");
    const start = lines.findIndex((line) => {
        return line.includes(opening);
    });
    if (start < 0) {
        throw new Error(`"${opening}" not found`);
    }
    const end = lines.findIndex((line, index) => {
        return index > start && line.startsWith("};");
    });

    return lines.slice(start + 1, end);
};

/**
 * Reads a `SYMBOL` table of `sql/lex.h`: the text of every entry and the
 * parser token it becomes.
 *
 * @param text The file.
 * @param name The table.
 *
 * @returns Text to token, in the table's order.
 */
const symbolTable = (text: string, name: string): Map<string, string> => {
    const entries = new Map<string, string>();
    for (const line of arrayBody(text, `SYMBOL ${name}[] =`)) {
        const match = /\{\s*"([^"]+)",\s*SYM\(([A-Z0-9_]+)\)/.exec(line);
        if (match) {
            entries.set(match[1], match[2]);
        }
    }

    return entries;
};

/**
 * Reads the parser tokens `sql_yacc.yy` lets stand for an identifier: the
 * members of every `keyword_*` rule, Oracle mode's included, since the
 * grammar highlights one SQL for both modes.
 *
 * @param text The file.
 *
 * @returns The token names.
 */
const unreservedTokens = (text: string): Set<string> => {
    const tokens = new Set<string>();
    let inKeywordRule = false;
    for (const line of text.split("\n")) {
        const rule = /^([a-z_]+):/.exec(line);
        if (rule) {
            inKeywordRule = /^(keyword_|non_reserved_keyword_udt)/
                .test(rule[1]);
        }
        // An alternative is the token alone, or after `|`; the `%ifdef`
        // lines and the actions in braces carry no tokens.
        if (inKeywordRule && !line.startsWith("%")) {
            const alternative = /^\s*\|?\s*([A-Z][A-Z0-9_]*)\s*(\{.*\})?\s*$/
                .exec(line);
            if (alternative) {
                tokens.add(alternative[1]);
            }
        }
    }

    return tokens;
};

/**
 * Reads the parser tokens `sql_yacc.yy` parses as a function call with a
 * syntax of its own - `DATE(`, `LEFT(`, `CHAR(... USING ...)`, `INSERT(`:
 * the alternatives of its `function_call_*` rules that start with a
 * token and `(`. Those are keywords everywhere else, which is how the
 * grammar treats them too: a function only before a parenthesis.
 *
 * @param text The file.
 *
 * @returns The token names.
 */
const functionTokens = (text: string): Set<string> => {
    const tokens = new Set<string>();
    let inFunctionRule = false;
    for (const line of text.split("\n")) {
        const rule = /^([a-z_]+):/.exec(line);
        if (rule) {
            inFunctionRule = rule[1].startsWith("function_call_");
        }
        if (inFunctionRule) {
            const call = /^\s*\|?\s*([A-Z][A-Z0-9_]*)\s*'\('/.exec(line);
            if (call) {
                tokens.add(call[1]);
            }
        }
    }

    return tokens;
};

/**
 * Reads the names of a `Native_func_registry` array.
 *
 * @param text The file.
 * @param name The array.
 *
 * @returns The function names.
 */
const nativeFunctions = (text: string, name: string): string[] => {
    const names: string[] = [];
    for (const line of arrayBody(text, `${name}[] =`)) {
        const match = /STRING_WITH_LEN\("([A-Z0-9_]+)"\)/.exec(line);
        if (match) {
            names.push(match[1]);
        }
    }

    return names;
};

/**
 * Reads the texts of one of `mrs_lexer.h`'s X-macro lists.
 *
 * @param text The file.
 * @param name The list macro.
 *
 * @returns The texts.
 */
const xMacroList = (text: string, name: string): string[] => {
    const start = text.indexOf(`#define ${name}(X)`);
    if (start < 0) {
        throw new Error(`${name} not found`);
    }
    // The list is one macro, continued with a backslash on every line
    // but its last.
    const lines: string[] = [];
    for (const line of text.slice(start).split("\n")) {
        lines.push(line);
        if (!line.trimEnd().endsWith("\\")) {
            break;
        }
    }
    const texts: string[] = [];
    for (const match of lines.join("\n")
        .matchAll(/X\([A-Z0-9_]+,\s*"([^"]+)"\)/g)) {
        texts.push(match[1]);
    }

    return texts;
};

/**
 * What a checkout is at, for the record.
 *
 * @param path The checkout.
 *
 * @returns Its description, or its commit.
 */
const describe = (path: string): string => {
    const git = (...args: string[]): string => {
        return execFileSync("git", ["-C", path, ...args],
            { encoding: "utf8" }).trim();
    };
    try {
        return git("describe", "--tags", "--always");
    } catch {
        return git("rev-parse", "HEAD");
    }
};

/** Sorted, unique, upper case: the lists are sets. */
const list = (words: Iterable<string>): string[] => {
    return [...new Set([...words].map((word) => {
        return word.toUpperCase();
    }))].sort();
};

/**
 * Reads the word lists out of the two checkouts into `words.json`.
 *
 * @param server The mariadb-server checkout.
 * @param shell The mariadb-shell checkout.
 *
 * @returns Nothing.
 */
const sync = (server: string, shell: string): void => {
    const read = (...parts: string[]): string => {
        return readFileSync(join(...parts), "utf8");
    };
    const lex = read(server, "sql", "lex.h");
    const symbols = symbolTable(lex, "symbols");
    const syntacticFunctions = symbolTable(lex, "sql_functions");
    const yacc = read(server, "sql", "sql_yacc.yy");
    const unreserved = unreservedTokens(yacc);
    // A token can have several spellings: SUBSTRING is SUBSTR and MID too.
    const callTokens = functionTokens(yacc);
    const keywordFunctions = [...symbols, ...syntacticFunctions]
        .filter(([, token]) => {
            return callTokens.has(token);
        }).map(([text]) => {
            return text;
        });

    // Words, not operators: `<=>` and `||` are in the same table.
    const keywords = [...symbols].filter(([text]) => {
        return /^[A-Z]/.test(text);
    });
    const words: IWords = {
        sources: {
            server: { path: server, commit: describe(server) },
            shell: { path: shell, commit: describe(shell) },
        },
        reserved: list(keywords.filter(([, token]) => {
            return !unreserved.has(token);
        }).map(([text]) => {
            return text;
        })),
        unreserved: list(keywords.filter(([, token]) => {
            return unreserved.has(token);
        }).map(([text]) => {
            return text;
        })),
        functions: list([
            ...syntacticFunctions.keys(),
            ...keywordFunctions,
            ...nativeFunctions(read(server, "sql", "item_create.cc"),
                "func_array"),
            ...nativeFunctions(read(server, "sql", "item_geofunc.cc"),
                "func_array_geom"),
        ]),
        restKeywords: list(xMacroList(
            read(shell, "modules", "mrs", "core", "mrs_lexer.h"),
            "MRS_KEYWORD_LIST")),
        restAnnotations: list(xMacroList(
            read(shell, "modules", "mrs", "core", "mrs_lexer.h"),
            "MRS_ANNOTATION_LIST")),
    };
    writeFileSync(wordsFile, `${JSON.stringify(words, null, 4)}\n`);
    console.log(`${wordsFile}: ${words.reserved.length} reserved, `
        + `${words.unreserved.length} unreserved, `
        + `${words.functions.length} functions, `
        + `${words.restKeywords.length} REST keywords, `
        + `${words.restAnnotations.length} annotations`);
};

/**
 * A regex alternation of words, longest first so that `IS` cannot win
 * over `ISSUER` where both could match.
 *
 * @param words The words.
 *
 * @returns The alternation, without its group.
 */
const alternation = (words: string[]): string => {
    return [...words].sort((a, b) => {
        return b.length - a.length || a.localeCompare(b);
    }).map((word) => {
        return word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }).join("|");
};

/**
 * Builds the grammar from the template and the word lists.
 *
 * @param check Compare with the file on disk instead of writing it.
 *
 * @returns Whether the grammar is as the template and lists make it.
 */
const build = (check: boolean): boolean => {
    const words = JSON.parse(readFileSync(wordsFile, "utf8")) as IWords;
    const template = readFileSync(templateFile, "utf8");
    // A REST keyword the server knows too (SELECT, CREATE, ...) stays a
    // server keyword: only the words REST SQL adds get its own scope.
    const serverKeywords = new Set([...words.reserved, ...words.unreserved]);
    const values: Record<string, string> = {
        RESERVED: alternation(words.reserved),
        UNRESERVED: alternation(words.unreserved),
        FUNCTIONS: alternation(words.functions),
        REST_KEYWORDS: alternation(words.restKeywords.filter((word) => {
            return !serverKeywords.has(word);
        })),
        REST_ANNOTATIONS: alternation(words.restAnnotations),
    };

    const grammar = template.replace(/\$\{([A-Z_]+)\}/g, (_, key) => {
        const value = values[key];
        if (value === undefined) {
            throw new Error(`template: unknown placeholder \${${key}}`);
        }

        return value;
    });
    // Parsed and printed again: the template is kept readable, the
    // grammar VS Code loads is kept valid.
    const text = `${JSON.stringify(JSON.parse(grammar), null, 4)}\n`;
    const file = join(syntaxes, grammarFile);
    if (!check) {
        writeFileSync(file, text);
        console.log(`${file} written`);

        return true;
    }

    let current: string | undefined;
    try {
        current = readFileSync(file, "utf8");
    } catch {
        current = undefined;
    }
    if (current !== text) {
        console.error(`${file} is out of date: run `
            + "`node tools/grammar.mts build`");

        return false;
    }

    return true;
};

/**
 * Reads one `--name value` option off the command line.
 *
 * @param args The arguments.
 * @param name The option.
 *
 * @returns Its value.
 */
const option = (args: string[], name: string): string => {
    const index = args.indexOf(`--${name}`);
    const value = args[index + 1];
    if (index < 0 || value === undefined) {
        throw new Error(`--${name} <path> is required`);
    }

    return value;
};

const [command, ...args] = process.argv.slice(2);
switch (command) {
    case "sync": {
        sync(option(args, "server"), option(args, "shell"));
        break;
    }
    case "build": {
        if (!build(args.includes("--check"))) {
            process.exit(1);
        }
        break;
    }
    default: {
        console.error("usage: node tools/grammar.mts sync --server <path> "
            + "--shell <path>\n       node tools/grammar.mts build [--check]");
        process.exit(2);
    }
}
