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
 * The most bytes the MCP server lets a connection's stored key take: its
 * list's prefix, the folder and the URI together. The shell's Windows
 * credential helper stores the key as a credential attribute, which Windows
 * caps at 256 bytes, and the server enforces that on every platform.
 */
export const MAX_CONNECTION_KEY_BYTES = 256;

/**
 * What the list's prefix takes of that: `MCP:CONN:` and `GUI:CONN:` are
 * both 9 bytes, so moving a connection between the two lists never changes
 * whether it fits.
 */
const CONNECTION_KEY_PREFIX_BYTES = 9;

/** What the folder and the URI may take together. */
export const CONNECTION_KEY_BUDGET =
    MAX_CONNECTION_KEY_BYTES - CONNECTION_KEY_PREFIX_BYTES;

const utf8 = new TextEncoder();

/**
 * How many bytes of {@link CONNECTION_KEY_BUDGET} a connection takes: the
 * URI, plus the folder and the `:` after it when it is not at the top level.
 * Counted in UTF-8, as the server counts, so a non-ASCII folder name costs
 * more than its length.
 *
 * @param uri The connection URI. The server stores it normalized, which can
 *            add a few bytes - the default port - to what the editor built,
 *            so the server's own check stays the last word.
 * @param path The folder.
 *
 * @returns The bytes taken.
 */
export const connectionKeyBytes = (uri: string, path: string): number => {
    const folder = normalizeFolder(path);

    return utf8.encode(uri).length
        + (folder === ROOT_FOLDER ? 0 : utf8.encode(folder).length + 1);
};

/**
 * Why a connection cannot be stored in a folder, if it cannot: the server
 * refuses a key longer than {@link MAX_CONNECTION_KEY_BYTES}.
 *
 * @param uri The connection URI.
 * @param path The folder.
 *
 * @returns The complaint, or undefined when it fits.
 */
export const connectionKeyProblem = (
    uri: string,
    path: string,
): string | undefined => {
    const taken = connectionKeyBytes(uri, path);
    if (taken <= CONNECTION_KEY_BUDGET) {
        return undefined;
    }

    const folder = normalizeFolder(path);
    const where = folder === ROOT_FOLDER
        ? "at the top level"
        : `in the folder '${folder}'`;

    return `The connection '${uri}' cannot be stored ${where}: its folder `
        + `and URI may take at most ${CONNECTION_KEY_BUDGET} bytes together, `
        + `and they take ${taken}. The secret store keys the password by `
        + `both, and Windows allows no more than ${MAX_CONNECTION_KEY_BYTES} `
        + "bytes per key. Use a shorter folder path or URI.";
};

/**
 * What is wrong with a folder path, if anything. A `/` separates folders, so
 * it is never part of a name; a `:` ends the path in the stored key, so the
 * server refuses a name holding one; and a folder that alone uses up
 * {@link CONNECTION_KEY_BUDGET} could never hold a connection.
 *
 * @param path The folder as typed.
 *
 * @returns The complaint, or undefined when the folder is fine.
 */
export const folderProblem = (path: string): string | undefined => {
    const bad = folderNames(path).find((name) => { return name.includes(":"); });
    if (bad !== undefined) {
        return `The folder name '${bad}' contains a ':', which a folder name `
            + "may not. Use '/' to put a folder inside another.";
    }

    // An empty URI: what is left of the budget is what a URI would get.
    const taken = connectionKeyBytes("", path);
    if (taken >= CONNECTION_KEY_BUDGET) {
        return `The folder path takes ${taken} of the `
            + `${CONNECTION_KEY_BUDGET} bytes a folder and a connection URI `
            + "may take together, which leaves no room for a URI. Use a "
            + "shorter folder path.";
    }

    return undefined;
};

/**
 * Why a set of connections cannot all be filed where they are going, if
 * they cannot - checked before any of them is moved, so a move is done
 * whole or not at all rather than stopping part way.
 *
 * @param filings Each connection's URI with the folder it is going to.
 *
 * @returns The complaint, naming every connection that does not fit, or
 *          undefined when they all do.
 */
export const filingProblem = (
    filings: Array<{ uri: string; path: string }>,
): string | undefined => {
    const misfits = filings.filter(({ uri, path }) => {
        return connectionKeyBytes(uri, path) > CONNECTION_KEY_BUDGET;
    });

    if (misfits.length === 0) {
        return undefined;
    }

    if (misfits.length === 1) {
        return connectionKeyProblem(misfits[0]!.uri, misfits[0]!.path);
    }

    const names = misfits.map(({ uri, path }) => {
        return `'${uri}' in '${normalizeFolder(path)}' `
            + `(${connectionKeyBytes(uri, path)} bytes)`;
    });

    return `${misfits.length} connections would not fit. A connection's `
        + `folder and URI may take at most ${CONNECTION_KEY_BUDGET} bytes `
        + "together, because the secret store keys the password by both and "
        + `Windows allows no more than ${MAX_CONNECTION_KEY_BYTES} bytes per `
        + `key: ${names.join(", ")}. Use a shorter folder path.`;
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
