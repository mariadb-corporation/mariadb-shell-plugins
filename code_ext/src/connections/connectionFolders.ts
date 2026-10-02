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
 * server keeps with a connection's other details, outside the secret store,
 * and reports back from `db.list_connections` in GUI mode. It is presentation
 * only: a connection is still identified by its URI, and nothing but this
 * extension ever sees the folder. The rules here mirror the server's
 * `normalize_connection_path`, so what the editor shows is what gets stored.
 * Any name goes, bar the `/` that separates them, and a folder never counts
 * against how long a stored key may be.
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
 * Connections in the order the Connections view shows them, folders
 * expanded: in each folder, its subfolders first, sorted by name, each
 * with everything in it, then the connections filed directly in it, in
 * the order they were given.
 *
 * @param connections The connections, each with its folder; none means
 *     the top level.
 *
 * @returns The same connections, reordered.
 */
export const inTreeOrder = <T extends { path?: string }>(
    connections: readonly T[],
): T[] => {
    const folderOf = (connection: T): string => {
        return connection.path ?? ROOT_FOLDER;
    };
    const folders = allFolders(connections.map(folderOf));

    // The same subfolder test the view's model applies, so the two agree.
    const contentsOf = (folder: string, depth: number): T[] => {
        const subfolders = folders.filter((path) => {
            return folderNames(path).length === depth + 1
                && (depth === 0 || path.startsWith(`${folder}/`));
        });

        return [
            ...subfolders.flatMap((path) => {
                return contentsOf(path, depth + 1);
            }),
            ...connections.filter((connection) => {
                return folderOf(connection) === folder;
            }),
        ];
    };

    return contentsOf(ROOT_FOLDER, 0);
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
