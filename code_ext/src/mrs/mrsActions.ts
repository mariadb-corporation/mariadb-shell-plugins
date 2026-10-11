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
    /**
     * Which action it is: an action is reported when it starts and again
     * when it ended, and the second replaces the first.
     */
    id: string;
    /** The connection it was done on; none for work on files only. */
    connectionId?: string;
    /**
     * What is being done while it runs, "Creating REST Service
     * '/myService'…", and what was done once it ended, "Created ...".
     */
    message: string;
    /** Whether it is still running. */
    pending?: boolean;
    /** The REST SQL it ran, a statement each. */
    statements?: IMrsStatement[];
    /** What it came to, where that says more: "2 schemas, 1 auth app". */
    detail?: string;
    /** What went wrong, where it failed. */
    error?: string;
    when: Date;
    elapsedMs: number;
}

/**
 * One REST SQL statement an action ran: a row under the action's, saying
 * what it does beside the statement itself, so the REST SQL can be learned
 * by reading.
 */
export interface IMrsStatement {
    /** The statement, on one line. */
    sql: string;
    /**
     * What it did, "Created REST Service '/myService'"; why it failed, or
     * that it was not run.
     */
    message: string;
    error?: string;
    elapsedMs?: number;
}

/** What an action is called while it runs, and once it is done. */
export interface IMrsWords {
    /** "Creating REST Service '/myService'…" */
    doing: string;
    /** "Created REST Service '/myService'" */
    done: string;
}

/**
 * @param doing What is being done, without the ellipsis.
 * @param done What was done.
 *
 * @returns Both, the first ending as running work does.
 */
export const words = (doing: string, done: string): IMrsWords => {
    return { doing: `${doing}\u2026`, done };
};

/**
 * @param said What an action is called.
 *
 * @returns What it is called once it failed: "Creating REST Service
 *          '/myService' failed", which a done form ("Created ...") beside
 *          an error would contradict.
 */
export const failedWords = (said: IMrsWords): string => {
    return `${said.doing.replace(/\u2026$/u, "")} failed`;
};

/** What each verb of REST SQL does, while running and once done. */
const VERBS: Record<string, [string, string]> = {
    "CREATE": ["Creating", "Created"],
    "CREATE OR REPLACE": ["Saving", "Saved"],
    "ALTER": ["Changing", "Changed"],
    "DROP": ["Deleting", "Deleted"],
    "SHOW": ["Reading", "Read"],
    "SHOW CREATE": ["Reading the definition of", "Read the definition of"],
    "USE": ["Switching to", "Switched to"],
    "CONFIGURE": ["Configuring", "Configured"],
    "GRANT": ["Granting", "Granted"],
    "REVOKE": ["Revoking", "Revoked"],
};

/** What a statement that says nothing more is called. */
const UNKNOWN = words("Running a REST statement", "Ran a REST statement");

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
 * @returns "Creating REST Service '/myService'…" while it runs, then
 *          "Created REST Service '/myService'".
 */
export const describeRestSql = (statements: string[]): IMrsWords => {
    const main = statements.find((statement) => {
        return !/^\s*USE\s/iu.test(statement);
    }) ?? statements[0] ?? "";

    return describeStatement(main) ?? UNKNOWN;
};

/**
 * Says what one REST SQL statement does.
 *
 * @param statement The statement.
 *
 * @returns Its words, or undefined where it is not one it knows.
 */
export const describeStatement = (
    statement: string,
): IMrsWords | undefined => {
    const tokens = statement.trim().split(/\s+/u);

    let at = 0;
    let verb = (tokens[at] ?? "").toUpperCase();
    at += 1;
    if (verb === "CREATE" && /^OR$/iu.test(tokens[at] ?? "")) {
        verb = "CREATE OR REPLACE";
        at += 2;
    } else if (verb === "SHOW" && /^CREATE$/iu.test(tokens[at] ?? "")) {
        verb = "SHOW CREATE";
        at += 1;
    }
    if (!/^REST$/iu.test(tokens[at] ?? "")) {
        return undefined;
    }
    at += 1;

    const rest = tokens.slice(at).join(" ").toUpperCase();
    const object = OBJECTS.find((candidate) => {
        return rest === candidate || rest.startsWith(`${candidate} `)
            || rest.startsWith(`${candidate};`);
    });
    if (object === undefined) {
        return undefined;
    }
    at += object.split(" ").length;
    if (/^IF$/iu.test(tokens[at] ?? "")) {
        at += 3;
    }
    // A quoted name can hold spaces: 'Full Access'.
    const quoted = /^(['"`])(.*?)\1(?:@(['"`])(.*?)\3)?/u.exec(
        tokens.slice(at).join(" "));
    const token = object === "METADATA" ? undefined : tokens[at];
    const name = quoted !== null && object !== "METADATA"
        ? ` '${quoted[2]}'${quoted[4] === undefined ? "" : `@'${quoted[4]}'`}`
        : token === undefined
            || NOT_A_NAME.test(token.split("=")[0].replace(/;$/u, ""))
            ? "" : ` '${unquote(token)}'`;
    const what = `REST ${titleCase(object
        .replace("METADATA STATUS", "SERVICE STATUS")
        .replace("AUTH", "AUTHENTICATION"))}`;

    const verbs = VERBS[verb];

    return verbs === undefined ? undefined
        : words(`${verbs[0]} ${what}${name}`, `${verbs[1]} ${what}${name}`);
};
