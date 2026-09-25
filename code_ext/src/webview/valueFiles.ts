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

import * as vscode from "vscode";

/**
 * Saving a grid value to a file and loading one from a file.
 *
 * A binary value travels as hex - that is how the server hands it over,
 * and how an edit writes it back - so the file is the bytes the hex
 * stands for, not the hex. That is the difference from the MySQL Shell,
 * which saves the text it holds a BLOB as and so writes a file of base64
 * rather than the picture that is in the column.
 */

/** Signatures that open a file of a known type, as bytes. */
const SIGNATURES: Array<{ bytes: number[]; extension: string }> = [
    { bytes: [0x89, 0x50, 0x4e, 0x47], extension: "png" },
    { bytes: [0xff, 0xd8, 0xff], extension: "jpg" },
    { bytes: [0x47, 0x49, 0x46, 0x38], extension: "gif" },
    { bytes: [0x25, 0x50, 0x44, 0x46], extension: "pdf" },
    { bytes: [0x50, 0x4b, 0x03, 0x04], extension: "zip" },
    { bytes: [0x1f, 0x8b], extension: "gz" },
];

/**
 * Guesses what kind of file a value is from what it starts with, so the
 * save dialog can offer a name the file can be opened by.
 *
 * @param bytes The value.
 *
 * @returns An extension without its dot: `png`, `svg`, ... or `bin` where
 *          nothing is recognised.
 */
export const extensionFor = (bytes: Uint8Array): string => {
    for (const signature of SIGNATURES) {
        if (signature.bytes.every((byte, index) => {
            return bytes[index] === byte;
        })) {
            return signature.extension;
        }
    }

    // RIFF....WEBP
    if (bytes.length >= 12
        && String.fromCharCode(...bytes.subarray(0, 4)) === "RIFF"
        && String.fromCharCode(...bytes.subarray(8, 12)) === "WEBP") {
        return "webp";
    }

    // Text: an SVG, somewhere in its first kilobyte, or else plain text if
    // it holds no control characters but line breaks and tabs.
    const head = new TextDecoder("utf-8", { fatal: false })
        .decode(bytes.subarray(0, 1024));
    if (/^﻿?\s*(<\?xml[^>]*>\s*)?(<!--[\s\S]*?-->\s*)*(<!DOCTYPE svg[^>]*>\s*)?<svg[\s>]/i
        .test(head)) {
        return "svg";
    }
    if (bytes.length > 0 && !/[\u0000-\u0008\u000e-\u001f�]/.test(head)) {
        return "txt";
    }

    return "bin";
};

/**
 * Turns a value as the grid holds it into the bytes it stands for.
 *
 * @param value Hex, with or without `0x`, as a binary value is held; or
 *              text, as a value typed into a text column is.
 *
 * @returns The bytes.
 */
export const bytesOf = (value: string): Uint8Array => {
    const hex = value.replace(/^0x/i, "");

    return /^([0-9a-f]{2})*$/i.test(hex)
        ? new Uint8Array(Buffer.from(hex, "hex"))
        : new TextEncoder().encode(value);
};

/**
 * @param bytes A file's content.
 *
 * @returns The hex a binary value is held as.
 */
export const hexOf = (bytes: Uint8Array): string => {
    return Buffer.from(bytes).toString("hex");
};

/**
 * Asks where to save a value and writes it there.
 *
 * @param value The value, as the grid holds it.
 * @param name What to call the file, without its extension.
 *
 * @returns Where it was saved, or undefined when the user cancelled.
 */
export const saveValueToFile = async (
    value: string,
    name: string,
): Promise<vscode.Uri | undefined> => {
    const bytes = bytesOf(value);
    const extension = extensionFor(bytes);
    const folder = vscode.workspace.workspaceFolders?.[0]?.uri;
    const fileName = `${name.replace(/[\\/:*?"<>|]/g, "_") || "value"}`
        + `.${extension}`;

    const target = await vscode.window.showSaveDialog({
        title: "Save Value to File",
        saveLabel: "Save",
        ...(folder ? { defaultUri: vscode.Uri.joinPath(folder, fileName) } : {}),
        filters: {
            [`${extension.toUpperCase()} file`]: [extension],
            "All files": ["*"],
        },
    });
    if (!target) {
        return undefined;
    }

    await vscode.workspace.fs.writeFile(target, bytes);

    return target;
};

/**
 * Asks for a file and reads it.
 *
 * @returns Its content as hex, or undefined when the user cancelled.
 */
export const loadValueFromFile = async (): Promise<string | undefined> => {
    const picked = await vscode.window.showOpenDialog({
        title: "Load Value from File",
        openLabel: "Load",
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
    });
    const target = picked?.[0];
    if (!target) {
        return undefined;
    }

    return hexOf(await vscode.workspace.fs.readFile(target));
};
