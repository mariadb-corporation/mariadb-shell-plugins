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
 * The folders connections are filed in.
 *
 * A folder is a path - `/Sandboxes`, `/Sandboxes/note_app` - that the MCP
 * server writes into a connection's stored key and reports back from
 * `db.list_connections` in GUI mode. It is presentation only: a connection is
 * still identified by its URI, and nothing but this extension ever sees the
 * folder. The rules here mirror the server's `normalize_connection_path`, so
 * what the editor shows is what gets stored.
 */

/** The folder a connection at the top level is in. */
export const ROOT_FOLDER = "/";

/**
 * A folder path in its one spelling: `/` for the top level, otherwise `/`
 * followed by the folder names joined with `/`. The leading slash may be left
 * out, and empty elements and blanks around a name are dropped.
 *
 * @param path The folder as typed.
 *
 * @returns The folder.
 */
export const normalizeFolder = (path: string): string => {
    const names = folderNames(path);

    return names.length === 0 ? ROOT_FOLDER : `/${names.join("/")}`;
};

/**
 * The names a folder path is made of, outermost first.
 *
 * @param path The folder.
 *
 * @returns The names; none for the top level.
 */
export const folderNames = (path: string): string[] => {
    return path
        .split("/")
        .map((name) => { return name.trim(); })
        .filter((name) => { return name !== ""; });
};

/**
 * What is wrong with a folder path, if anything. A `/` separates folders, so
 * it is never part of a name; a `:` ends the path in the stored key, so the
 * server refuses a name holding one.
 *
 * @param path The folder as typed.
 *
 * @returns The complaint, or undefined when the folder is fine.
 */
export const folderProblem = (path: string): string | undefined => {
    const bad = folderNames(path).find((name) => { return name.includes(":"); });

    return bad === undefined
        ? undefined
        : `The folder name '${bad}' contains a ':', which a folder name may `
        + "not. Use '/' to put a folder inside another.";
};

/**
 * Every folder the given ones imply, parents included, sorted - what the
 * editor offers to pick from.
 *
 * @param paths The folders connections are filed in.
 *
 * @returns Each folder once, the top level left out.
 */
export const allFolders = (paths: Iterable<string>): string[] => {
    const folders = new Set<string>();
    for (const path of paths) {
        const names = folderNames(path);
        for (let depth = 1; depth <= names.length; depth += 1) {
            folders.add(`/${names.slice(0, depth).join("/")}`);
        }
    }

    return [...folders].sort((left, right) => {
        return left.localeCompare(right);
    });
};

/**
 * The deepest folder every one of the given folders is in or is.
 *
 * @param paths The folders; none means the top level.
 *
 * @returns Their common folder; `/` when they share none.
 */
export const commonFolder = (paths: string[]): string => {
    if (paths.length === 0) {
        return ROOT_FOLDER;
    }

    const [first, ...rest] = paths.map(folderNames);
    const shared: string[] = [];
    for (const [index, name] of first!.entries()) {
        if (!rest.every((names) => { return names[index] === name; })) {
            break;
        }
        shared.push(name);
    }

    return normalizeFolder(shared.join("/"));
};
