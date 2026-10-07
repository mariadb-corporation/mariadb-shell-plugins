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
 * Small text helpers shared by the extension host and the webviews. This
 * module must stay free of `vscode` so the webview bundles can import it.
 */

/**
 * @param error Whatever a `catch` clause received.
 *
 * @returns The message of an `Error`, or the value as text otherwise.
 */
export const errorText = (error: unknown): string => {
    return error instanceof Error ? error.message : String(error);
};

/**
 * @param count How many of something came back.
 * @param singular What one of them is called.
 *
 * @returns `1 schema`, `4 schemas`.
 */
export const counted = (count: number, singular: string): string => {
    return `${count} ${singular}${count === 1 ? "" : "s"}`;
};
