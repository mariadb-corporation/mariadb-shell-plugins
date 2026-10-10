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
 * What the MariaDB REST Service did, in the words of the Actions tab: one
 * action per thing the user would name - a service created, a tree row
 * listed, a client SDK dumped - however many REST SQL statements or tool
 * calls it took. No REST SQL appears in it.
 */
export interface IMrsAction {
    /** The connection it was done on; none for work on files only. */
    connectionId?: string;
    /** What was done: "Created REST Service '/myService'". */
    message: string;
    /** What it came to, where that says more: "2 schemas, 1 auth app". */
    detail?: string;
    /** What went wrong, where it failed. */
    error?: string;
    when: Date;
    elapsedMs: number;
}

/** What each verb of REST SQL did, in the past tense. */
const VERBS: Record<string, string> = {
    "CREATE": "Created",
    "CREATE OR REPLACE": "Saved",
    "ALTER": "Changed",
    "DROP": "Deleted",
    "SHOW CREATE": "Read the definition of",
    "CONFIGURE": "Configured",
    "GRANT": "Granted",
    "REVOKE": "Revoked",
};

/** The REST objects a statement can be about, longest first. */
const OBJECTS = [
    "METADATA SCHEMAS", "METADATA SCHEMA", "METADATA STATUS", "METADATA",
    "CONTENT SETS", "CONTENT SET", "CONTENT FILES", "CONTENT FILE",
    "AUTH APPS", "AUTH APP", "AUTH VENDORS", "SERVICES", "SERVICE",
    "SCHEMAS", "SCHEMA", "VIEWS", "VIEW", "PROCEDURES", "PROCEDURE",
    "FUNCTIONS", "FUNCTION", "SCRIPTS", "SCRIPT", "USERS", "USER",
    "ROLES", "ROLE", "DAEMONS", "DAEMON", "COLUMNS",
];

/** Words after an object that are not its name. */
const NOT_A_NAME = /^(?:ON|FOR|FROM|IN|FORMAT|WHERE|TO|AS|IF|INCLUDING)$/iu;

/**
 * @param token A name as REST SQL writes it.
 *
 * @returns It without its quotes, and a trailing semicolon.
 */
const unquote = (token: string): string => {
    return token.replace(/;$/u, "").replace(/^[`'"](.*)[`'"]$/u, "$1");
};

/**
 * @param words Upper case words.
 *
 * @returns Them in Title Case: "CONTENT SET" is "Content Set".
 */
const titleCase = (words: string): string => {
    return words.toLowerCase().replace(/\b\w/gu, (first) => {
        return first.toUpperCase();
    });
};

/**
 * Says in plain words what a call's REST SQL did, by the statement that
 * matters: the first that is not a USE. What came of a list is left to
 * the caller, which knows what it read.
 *
 * @param statements The statements of the call.
 *
 * @returns "Created REST Service '/myService'", "Read REST Services".
 */
export const describeRestSql = (statements: string[]): string => {
    const main = statements.find((statement) => {
        return !/^\s*USE\s/iu.test(statement);
    }) ?? statements[0] ?? "";
    const words = main.trim().split(/\s+/u);

    let at = 0;
    let verb = (words[at] ?? "").toUpperCase();
    at += 1;
    if (verb === "CREATE" && /^OR$/iu.test(words[at] ?? "")) {
        verb = "CREATE OR REPLACE";
        at += 2;
    } else if (verb === "SHOW" && /^CREATE$/iu.test(words[at] ?? "")) {
        verb = "SHOW CREATE";
        at += 1;
    }
    if (!/^REST$/iu.test(words[at] ?? "")) {
        return "Ran a REST statement";
    }
    at += 1;

    const rest = words.slice(at).join(" ").toUpperCase();
    const object = OBJECTS.find((candidate) => {
        return rest === candidate || rest.startsWith(`${candidate} `)
            || rest.startsWith(`${candidate};`);
    });
    if (object === undefined) {
        return "Ran a REST statement";
    }
    at += object.split(" ").length;
    if (/^IF$/iu.test(words[at] ?? "")) {
        at += 3;
    }
    const token = object === "METADATA" ? undefined : words[at];
    const name = token === undefined
        || NOT_A_NAME.test(token.split("=")[0].replace(/;$/u, ""))
        ? "" : ` '${unquote(token)}'`;
    const what = `REST ${titleCase(object
        .replace("METADATA STATUS", "SERVICE STATUS")
        .replace("AUTH", "AUTHENTICATION"))}`;

    if (verb === "SHOW") {
        return `Read ${what}${name}`;
    }
    const done = VERBS[verb];

    return done === undefined ? "Ran a REST statement" : `${done} ${what}${name}`;
};
