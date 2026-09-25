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

import type { ValueKind } from "./protocol.js";
import { bytesOf, extensionFor, hexOf } from "./valueFiles.js";

/** The scheme a grid value is opened in an editor under. */
export const VALUE_SCHEME = "mariadb-value";

/** A grid value to open in an editor. */
export interface IValueToOpen {
    /** The value as the grid holds it: text, or hex for a binary value. */
    value: string | null;
    kind: ValueKind;
    /** What the document is called, without an extension. */
    name: string;
    /** True where the value cannot be written back. */
    readOnly: boolean;
    /**
     * Writes a saved document back to the cell it came from. It rejects,
     * with a reason, when the cell is gone - which fails the save.
     */
    writeBack(value: string): Promise<void>;
}

/** One opened value. */
interface IValueDocument extends IValueToOpen {
    content: Uint8Array;
    mtime: number;
}

/**
 * Picks the file extension a value is opened under, which is what gives
 * its editor a language: JSON for a JSON column and for text that is
 * JSON, XML for text that looks like it, the file type a binary value's
 * content is (an image opens in VS Code's image preview), and plain text
 * for the rest.
 *
 * @param kind How the grid holds the value.
 * @param content The document's bytes.
 * @param text The value as text, where it is text.
 *
 * @returns The extension, without its dot.
 */
export const extensionOfValue = (
    kind: ValueKind,
    content: Uint8Array,
    text: string,
): string => {
    if (kind === "binary") {
        return extensionFor(content);
    }
    if (kind === "json") {
        return "json";
    }

    const trimmed = text.trim();
    if (/^[[{]/.test(trimmed)) {
        try {
            JSON.parse(trimmed);

            return "json";
        } catch {
            // Only looks like it.
        }
    }

    return /^<[?!a-zA-Z]/.test(trimmed) ? "xml" : "txt";
};

/**
 * Grid values as files, so any of them can be viewed and edited in an
 * ordinary VS Code editor - with the language, formatting and validation
 * its extension gives it, in the user's own theme and keybindings - with
 * nothing bundled into the webview.
 *
 * Saving a document does not write a file anywhere: it puts the value
 * back into the grid as a pending edit, which Apply writes like any other.
 * A value that cannot be written back is read only, and a save whose cell
 * has gone - the result set refreshed, paged or closed - fails with why.
 */
export class ValueDocuments implements vscode.FileSystemProvider {
    readonly #documents = new Map<string, IValueDocument>();
    readonly #changed = new vscode.EventEmitter<vscode.FileChangeEvent[]>();

    public readonly onDidChangeFile = this.#changed.event;

    /**
     * Puts a value where an editor can open it, replacing what the same
     * cell was opened with before - an editor still showing that one
     * reloads it.
     *
     * @param key What tells this cell from every other.
     * @param toOpen The value, and where it goes back to.
     *
     * @returns The document's URI.
     */
    public put(key: string, toOpen: IValueToOpen): vscode.Uri {
        const text = toOpen.value ?? "";
        const content = toOpen.kind === "binary"
            ? bytesOf(text)
            : new TextEncoder().encode(text);
        const extension = extensionOfValue(toOpen.kind, content, text);
        const name = toOpen.name.replace(/[\\/:*?"<>|]/g, "_") || "value";
        const uri = vscode.Uri.from({
            scheme: VALUE_SCHEME,
            path: `/${encodeURIComponent(key)}/${name}.${extension}`,
        });

        const existed = this.#documents.has(uri.path);
        this.#documents.set(uri.path, {
            ...toOpen,
            content,
            mtime: Date.now(),
        });
        if (existed) {
            this.#changed.fire([{ type: vscode.FileChangeType.Changed, uri }]);
        }

        return uri;
    }

    /**
     * Drops a document that is no longer open.
     *
     * @param uri The document.
     *
     * @returns Nothing.
     */
    public forget(uri: vscode.Uri): void {
        if (uri.scheme === VALUE_SCHEME) {
            this.#documents.delete(uri.path);
        }
    }

    /** @returns How many documents are held. */
    public get size(): number {
        return this.#documents.size;
    }

    public watch(): vscode.Disposable {
        // Nothing changes behind an editor's back but what `put` fires.
        return { dispose: () => { /* nothing to stop */ } };
    }

    public stat(uri: vscode.Uri): vscode.FileStat {
        const document = this.#document(uri);

        return {
            type: vscode.FileType.File,
            ctime: document.mtime,
            mtime: document.mtime,
            size: document.content.byteLength,
            ...(document.readOnly
                ? { permissions: vscode.FilePermission.Readonly }
                : {}),
        };
    }

    public readDirectory(): Array<[string, vscode.FileType]> {
        return [];
    }

    public createDirectory(): void {
        throw vscode.FileSystemError.NoPermissions(
            "Values are opened, not created.");
    }

    public readFile(uri: vscode.Uri): Uint8Array {
        return this.#document(uri).content;
    }

    public async writeFile(uri: vscode.Uri, content: Uint8Array): Promise<void> {
        const document = this.#document(uri);
        if (document.readOnly) {
            throw vscode.FileSystemError.NoPermissions(
                "This value cannot be written back to its result set.");
        }

        const value = document.kind === "binary"
            ? hexOf(content)
            : new TextDecoder().decode(content);
        try {
            await document.writeBack(value);
        } catch (error) {
            throw vscode.FileSystemError.Unavailable(
                error instanceof Error ? error.message : String(error));
        }

        document.content = content;
        document.mtime = Date.now();
        this.#changed.fire([{ type: vscode.FileChangeType.Changed, uri }]);
    }

    public delete(): void {
        throw vscode.FileSystemError.NoPermissions(
            "A value is removed by setting it to NULL in the grid.");
    }

    public rename(): void {
        throw vscode.FileSystemError.NoPermissions(
            "A value cannot be renamed.");
    }

    /**
     * @param uri A document's URI.
     *
     * @returns The document.
     */
    #document(uri: vscode.Uri): IValueDocument {
        const document = this.#documents.get(uri.path);
        if (!document) {
            throw vscode.FileSystemError.FileNotFound(uri);
        }

        return document;
    }
}
