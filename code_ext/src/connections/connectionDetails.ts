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

import { CONNECTION_COLORS, type ConnectionColor } from "../mcp/types.js";

/**
 * What the MCP server accepts for a connection: how long its URI may be, and
 * what its caption and color may be.
 *
 * The rules mirror the server's (`check_connection_key_length`,
 * `normalize_connection_caption`, `normalize_connection_color`), so the
 * editor can say what is wrong while it is typed; the server's own check
 * stays the last word. The folder, caption and color are kept outside the
 * secret store, so only the URI counts against the key's length.
 */

/**
 * The most bytes the MCP server lets a connection's stored key take: its
 * list's prefix and the URI together. The shell's Windows credential helper
 * stores the key as a credential attribute, which Windows caps at 256
 * bytes, and the server enforces that on every platform.
 */
export const MAX_CONNECTION_KEY_BYTES = 256;

/**
 * What the list's prefix takes of that: `MCP:CONN:` and `GUI:CONN:` are
 * both 9 bytes, so moving a connection between the two lists never changes
 * whether it fits.
 */
const CONNECTION_KEY_PREFIX_BYTES = 9;

/** What the URI may take. */
export const CONNECTION_KEY_BUDGET =
    MAX_CONNECTION_KEY_BYTES - CONNECTION_KEY_PREFIX_BYTES;

/** The most characters a caption may have. */
export const MAX_CAPTION_LENGTH = 100;

const utf8 = new TextEncoder();

/**
 * Why a connection URI is too long to store, if it is. Counted in UTF-8, as
 * the server counts.
 *
 * @param uri The connection URI. The server stores it normalized, which can
 *            add a few bytes - the default port - to what the editor built.
 *
 * @returns The complaint, or undefined when it fits.
 */
export const connectionKeyProblem = (uri: string): string | undefined => {
    const taken = utf8.encode(uri).length;
    if (taken <= CONNECTION_KEY_BUDGET) {
        return undefined;
    }

    return `The connection '${uri}' cannot be stored: its URI may take at `
        + `most ${CONNECTION_KEY_BUDGET} bytes, and it takes ${taken}. The `
        + "secret store keys the password by it, and Windows allows no more "
        + `than ${MAX_CONNECTION_KEY_BYTES} bytes per key. Use a shorter URI.`;
};

/**
 * A caption in the one form it is stored in: without surrounding blanks.
 *
 * @param caption The caption as typed.
 *
 * @returns The caption; `""` for none.
 */
export const normalizeCaption = (caption: string): string => {
    return caption.trim();
};

/**
 * What is wrong with a caption, if anything.
 *
 * @param caption The caption as typed.
 *
 * @returns The complaint, or undefined when it is fine.
 */
export const captionProblem = (caption: string): string | undefined => {
    const normalized = normalizeCaption(caption);

    if (/[\u0000-\u001f\u007f]/u.test(normalized)) {
        return "The caption must be a single line of text.";
    }

    if (normalized.length > MAX_CAPTION_LENGTH) {
        return `The caption may have at most ${MAX_CAPTION_LENGTH} `
            + `characters, and it has ${normalized.length}.`;
    }

    return undefined;
};

/**
 * Whether a value names a connection color.
 *
 * @param value Anything.
 *
 * @returns True when it is one of `CONNECTION_COLORS`.
 */
export const isConnectionColor = (value: unknown): value is ConnectionColor => {
    return CONNECTION_COLORS.some((color) => { return color === value; });
};
