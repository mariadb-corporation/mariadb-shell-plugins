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
 * What the dump, load, copy, export and import dialog asks for, one
 * operation at a time: the path or the target, and the utility's options.
 *
 * Plain data and pure functions, shared by the extension, which builds the
 * operation and checks what comes back, and the webview, which draws it.
 * The values are what the inputs hold (text, or a box ticked); only an
 * option that differs from the shell's own default goes to the utility, so
 * the shell stays the one deciding what a default is.
 */

import type { UtilOptions } from "../mcp/utilApi.js";

export type UtilOperation =
    | "dumpInstance" | "dumpSchemas" | "dumpTables" | "exportTable"
    | "loadDump" | "importTable"
    | "copyInstance" | "copySchemas" | "copyTables";

/** How an option is entered. */
export type UtilOptionType = "bool" | "number" | "text" | "list" | "choice";

export interface IUtilOptionSpec {
    /** The shell's option name, camelCase. */
    name: string;
    label: string;
    type: UtilOptionType;
    /**
     * The shell's default: a bool for "bool", the choice for "choice", and
     * for the others the text the input starts with.
     */
    initial: boolean | string;
    hint: string;
    choices?: string[];
    /** Shown on the Advanced tab rather than the Basic one. */
    advanced?: boolean;
}

/** What the path field is: where the work reads or writes. */
export interface IUtilPathSpec {
    label: string;
    /** What the Browse button opens. */
    browse: "folder" | "saveFile" | "openFiles";
    hint: string;
}

export interface IUtilOperationSpec {
    operation: UtilOperation;
    /** The dialog's title, which also names the task. */
    title: string;
    description: string;
    /** The button that starts it. */
    action: string;
    path?: IUtilPathSpec;
    /** A copy asks for the connection to copy to. */
    target?: boolean;
    options: IUtilOptionSpec[];
}

/** What the dialog holds: the path, the target and every option. */
export type UtilValues = Record<string, boolean | string>;

export interface IUtilProblem {
    field: string;
    message: string;
}

/** What the operation is started on: the row's names. */
export interface IUtilSubject {
    /** The connection's address, for the title. */
    connection: string;
    schemas?: string[];
    tables?: string[];
}

// --- the options -------------------------------------------------------------

const opt = (
    name: string, label: string, type: UtilOptionType,
    initial: boolean | string, hint: string,
    extra: Partial<IUtilOptionSpec> = {},
): IUtilOptionSpec => {
    return { name, label, type, initial, hint, ...extra };
};

const threads = (initial: string): IUtilOptionSpec => {
    return opt("threads", "Threads", "number", initial,
        "Parallel connections reading or writing the data.");
};

const dryRun = opt("dryRun", "Dry run", "bool", false,
    "Check everything and report what would be done, without doing it.");

/** The options of every dump, and of the dump half of a copy. */
const dumpCommon = (forCopy: boolean): IUtilOptionSpec[] => {
    return [
        threads("4"),
        opt("consistent", "Consistent snapshot", "bool", true,
            "Read all data at one point in time, with a brief global lock."),
        opt("ddlOnly", "Definitions only", "bool", false,
            "Only the CREATE statements, no rows."),
        opt("dataOnly", "Data only", "bool", false,
            "Only the rows, no CREATE statements."),
        ...(forCopy ? [] : [
            opt("compression", "Compression", "choice", "zstd",
                "How the data files are compressed.",
                { choices: ["zstd", "gzip", "none"] }),
        ]),
        opt("maxRate", "Maximum rate", "text", "",
            "Bytes per second per thread, such as 10M. Empty for no limit.",
            { advanced: true }),
        ...(forCopy ? [] : [
            opt("bytesPerChunk", "Chunk size", "text", "",
                "The size of the chunks a table is split into, such as 128M. "
                + "Empty for the default, 64M.", { advanced: true }),
        ]),
        opt("tzUtc", "Times in UTC", "bool", true,
            "Dump TIMESTAMP values in UTC, so a load in another time zone "
            + "keeps them.", { advanced: true }),
    ];
};

const objectTypes = [
    opt("triggers", "Triggers", "bool", true, "Include the triggers.",
        { advanced: true }),
    opt("routines", "Routines", "bool", true,
        "Include stored procedures and functions.", { advanced: true }),
    opt("events", "Events", "bool", true, "Include the events.",
        { advanced: true }),
];

const tableFilters = [
    opt("includeTables", "Include tables", "list", "",
        "Only these tables, as schema.table, comma separated.",
        { advanced: true }),
    opt("excludeTables", "Exclude tables", "list", "",
        "Leave these tables out, as schema.table, comma separated.",
        { advanced: true }),
];

/** The options of every load, and of the load half of a copy. */
const loadCommon = (forCopy: boolean): IUtilOptionSpec[] => {
    return [
        ...(forCopy ? [] : [threads("4")]),
        opt("dropExistingObjects", "Drop existing objects", "bool", false,
            "Drop an object the target has already before creating it."),
        opt("ignoreExistingObjects", "Ignore existing objects", "bool", false,
            "Leave an object the target has already as it is, and load the "
            + "rest."),
        opt("ignoreVersion", "Ignore the server version", "bool", false,
            "Load also where the source's major version differs from the "
            + "target's.", { advanced: true }),
        opt("deferTableIndexes", "Defer table indexes", "choice", "fulltext",
            "Create these secondary indexes after the rows are loaded, "
            + "which is faster.",
            { choices: ["fulltext", "all", "off"], advanced: true }),
        opt("analyzeTables", "Analyze tables", "choice", "off",
            "Update the table statistics after the load.",
            { choices: ["off", "on", "histogram"], advanced: true }),
        opt("skipBinlog", "Skip the binary log", "bool", false,
            "Do not write the loaded statements to the target's binary log.",
            { advanced: true }),
    ];
};

const renameSchema = (hint: string): IUtilOptionSpec => {
    return opt("schema", "Into schema", "text", "", hint);
};

const join = (names: string[] | undefined): string => {
    return (names ?? []).join(", ");
};

/**
 * @param operation What to do.
 * @param subject What it is done on.
 *
 * @returns The dialog for it.
 */
export const utilOperationSpec = (
    operation: UtilOperation,
    subject: IUtilSubject,
): IUtilOperationSpec => {
    const schemas = join(subject.schemas);
    const tables = join(subject.tables);
    const dumpFolder: IUtilPathSpec = {
        label: "Output Folder",
        browse: "folder",
        hint: "The folder to write the dump to. It must not exist or be "
            + "empty.",
    };

    switch (operation) {
        case "dumpInstance": {
            return {
                operation, action: "Dump",
                title: `Dump ${subject.connection} to Disk`,
                description: "Dumps every schema of the server, and its user "
                    + "accounts, to a folder. Load it with Load Dump from "
                    + "Disk.",
                path: dumpFolder,
                options: [
                    ...dumpCommon(false),
                    opt("users", "User accounts", "bool", true,
                        "Include the user accounts and their grants."),
                    opt("excludeSchemas", "Exclude schemas", "list", "",
                        "Leave these schemas out, comma separated."),
                    ...objectTypes, ...tableFilters, dryRun,
                ],
            };
        }

        case "dumpSchemas": {
            return {
                operation, action: "Dump",
                title: `Dump ${schemas} to Disk`,
                description: "Dumps the schemas to a folder. Load it with "
                    + "Load Dump from Disk.",
                path: dumpFolder,
                options: [...dumpCommon(false), ...objectTypes,
                    ...tableFilters, dryRun],
            };
        }

        case "dumpTables": {
            return {
                operation, action: "Dump",
                title: `Dump ${tables} to Disk`,
                description: `Dumps tables of ${schemas} to a folder. Load `
                    + "it with Load Dump from Disk.",
                path: dumpFolder,
                options: [
                    ...dumpCommon(false),
                    opt("triggers", "Triggers", "bool", true,
                        "Include the tables' triggers.", { advanced: true }),
                    dryRun,
                ],
            };
        }

        case "exportTable": {
            return {
                operation, action: "Export",
                title: `Export ${schemas}.${tables} to a File`,
                description: "Writes the table's rows to one file, which "
                    + "Import Data from File reads back.",
                path: {
                    label: "Output File", browse: "saveFile",
                    hint: "The file to write.",
                },
                options: [
                    opt("dialect", "Format", "choice", "default",
                        "default is tab separated with backslash escapes, "
                        + "as LOAD DATA reads it by default.",
                        { choices: ["default", "csv", "csv-unix", "tsv"] }),
                    opt("compression", "Compression", "choice", "none",
                        "Compress the file.",
                        { choices: ["none", "gzip", "zstd"] }),
                    opt("where", "Rows where", "text", "",
                        "A condition the rows must meet, such as id > 1000. "
                        + "Empty for every row."),
                    opt("maxRate", "Maximum rate", "text", "",
                        "Bytes per second, such as 10M. Empty for no limit.",
                        { advanced: true }),
                ],
            };
        }

        case "loadDump": {
            return {
                operation, action: "Load",
                title: `Load a Dump into ${subject.connection}`,
                description: "Loads a dump made with Dump to Disk. A load "
                    + "that was stopped resumes where it stopped when it is "
                    + "started again.",
                path: {
                    label: "Dump Folder", browse: "folder",
                    hint: "The folder holding the dump.",
                },
                options: [
                    renameSchema("Load a dump of one schema into this schema "
                        + "instead. Empty keeps the dumped name."),
                    ...loadCommon(false),
                    opt("loadUsers", "User accounts", "bool", false,
                        "Create the dump's user accounts too."),
                    opt("loadData", "Rows", "bool", true,
                        "Load the rows.", { advanced: true }),
                    opt("loadDdl", "Definitions", "bool", true,
                        "Run the CREATE statements.", { advanced: true }),
                    opt("resetProgress", "Start over", "bool", false,
                        "Forget what an earlier attempt loaded, and load "
                        + "everything again.", { advanced: true }),
                    opt("includeSchemas", "Include schemas", "list", "",
                        "Only these schemas, comma separated.",
                        { advanced: true }),
                    opt("excludeSchemas", "Exclude schemas", "list", "",
                        "Leave these schemas out, comma separated.",
                        { advanced: true }),
                    ...tableFilters, dryRun,
                ],
            };
        }

        case "importTable": {
            return {
                operation, action: "Import",
                title: `Import Data into ${schemas}.${tables}`,
                description: "Loads files of rows into the table, in "
                    + "parallel. The table must exist.",
                path: {
                    label: "Files", browse: "openFiles",
                    hint: "The files to import, comma separated. Names may "
                        + "contain * and ?.",
                },
                options: [
                    opt("schema", "Schema", "text", subject.schemas?.[0] ?? "",
                        "The schema of the table."),
                    opt("table", "Table", "text", subject.tables?.[0] ?? "",
                        "The table to load into."),
                    opt("dialect", "Format", "choice", "default",
                        "As the file was written: default is tab separated "
                        + "with backslash escapes.",
                        { choices: ["default", "csv", "csv-unix", "tsv",
                            "json"] }),
                    opt("skipRows", "Skip lines", "number", "",
                        "Lines to skip at the start of each file, such as 1 "
                        + "for a header."),
                    opt("columns", "Columns", "list", "",
                        "The table's columns the fields go to, in order, "
                        + "comma separated. Empty for all, in table order."),
                    opt("replaceDuplicates", "Replace duplicates", "bool",
                        false, "Replace a row whose key exists already, "
                        + "instead of skipping it."),
                    threads("8"),
                    opt("characterSet", "Character set", "text", "",
                        "The files' character set. Empty for utf8mb4.",
                        { advanced: true }),
                    opt("maxRate", "Maximum rate", "text", "",
                        "Bytes per second per thread, such as 10M. Empty for "
                        + "no limit.", { advanced: true }),
                ],
            };
        }

        case "copyInstance": {
            return {
                operation, action: "Copy", target: true,
                title: `Copy ${subject.connection} to Another Server`,
                description: "Copies every schema, and the user accounts, to "
                    + "another server, without writing files. The target "
                    + "needs local_infile switched on.",
                options: [
                    ...dumpCommon(true),
                    opt("users", "User accounts", "bool", true,
                        "Copy the user accounts and their grants."),
                    opt("excludeSchemas", "Exclude schemas", "list", "",
                        "Leave these schemas out, comma separated."),
                    ...loadCommon(true), ...objectTypes, ...tableFilters,
                    dryRun,
                ],
            };
        }

        case "copySchemas": {
            return {
                operation, action: "Copy", target: true,
                title: `Copy ${schemas} to Another Server`,
                description: "Copies the schemas to another server, without "
                    + "writing files. The target needs local_infile switched "
                    + "on.",
                options: [
                    ...(subject.schemas?.length === 1
                        ? [renameSchema("Copy into this schema on the target "
                            + "instead. Empty keeps the name.")]
                        : []),
                    ...dumpCommon(true), ...loadCommon(true), ...objectTypes,
                    ...tableFilters, dryRun,
                ],
            };
        }

        case "copyTables": {
            return {
                operation, action: "Copy", target: true,
                title: `Copy ${tables} to Another Server`,
                description: `Copies tables of ${schemas} to another server, `
                    + "without writing files. The target needs local_infile "
                    + "switched on.",
                options: [
                    renameSchema("Copy into this schema on the target instead. "
                        + "Empty keeps the name."),
                    ...dumpCommon(true), ...loadCommon(true), dryRun,
                ],
            };
        }

        default: {
            throw new Error(`Unknown operation ${String(operation)}`);
        }
    }
};

/**
 * @param spec The dialog.
 * @param path The path to start with.
 *
 * @returns The values the dialog starts with: every option at its default.
 */
export const initialUtilValues = (
    spec: IUtilOperationSpec,
    path = "",
): UtilValues => {
    const values: UtilValues = {};
    if (spec.path !== undefined) {
        values.path = path;
    }
    if (spec.target === true) {
        values.target = "";
    }
    for (const option of spec.options) {
        values[option.name] = option.initial;
    }

    return values;
};

/** Splits a comma or line separated list, dropping empty entries. */
export const splitList = (text: string): string[] => {
    return text.split(/[,\n]/u)
        .map((entry) => { return entry.trim(); })
        .filter((entry) => { return entry !== ""; });
};

/**
 * Checks the values and builds the utility's options from them.
 *
 * @param spec The dialog.
 * @param values What it holds.
 *
 * @returns The options, holding only what differs from a default, and the
 *          path and target; or the first problem.
 */
export const buildUtilOptions = (
    spec: IUtilOperationSpec,
    values: UtilValues,
): { options: UtilOptions; paths: string[]; target?: string } | {
    problem: IUtilProblem;
} => {
    let paths: string[] = [];
    if (spec.path !== undefined) {
        const path = String(values.path ?? "").trim();
        paths = spec.path.browse === "openFiles" ? splitList(path)
            : path === "" ? [] : [path];
        if (paths.length === 0) {
            return {
                problem: {
                    field: "path",
                    message: `${spec.path.label} must be given.`,
                },
            };
        }
    }

    const target = String(values.target ?? "");
    if (spec.target === true && target === "") {
        return {
            problem: {
                field: "target", message: "Choose the connection to copy to.",
            },
        };
    }

    const options: UtilOptions = {};
    for (const option of spec.options) {
        const value = values[option.name] ?? option.initial;
        switch (option.type) {
            case "bool": {
                if (value !== option.initial) {
                    options[option.name] = value === true;
                }
                break;
            }

            case "choice": {
                if (value !== option.initial) {
                    options[option.name] = String(value);
                }
                break;
            }

            case "number": {
                const text = String(value).trim();
                if (text === "" || text === option.initial) {
                    break;
                }
                if (!/^\d+$/u.test(text) || Number(text) < 0) {
                    return {
                        problem: {
                            field: option.name,
                            message: `${option.label} must be a whole number.`,
                        },
                    };
                }
                options[option.name] = Number(text);
                break;
            }

            case "list": {
                const entries = splitList(String(value));
                if (entries.length > 0) {
                    options[option.name] = entries;
                }
                break;
            }

            default: {
                const text = String(value).trim();
                if (text !== "") {
                    options[option.name] = text;
                }
            }
        }
    }

    if (options.ddlOnly === true && options.dataOnly === true) {
        return {
            problem: {
                field: "dataOnly",
                message: "Definitions only and data only cannot both be set.",
            },
        };
    }
    if (options.dropExistingObjects === true
        && options.ignoreExistingObjects === true) {
        return {
            problem: {
                field: "ignoreExistingObjects",
                message: "Existing objects cannot be both dropped and "
                    + "ignored.",
            },
        };
    }

    return { options, paths, ...(spec.target === true ? { target } : {}) };
};
