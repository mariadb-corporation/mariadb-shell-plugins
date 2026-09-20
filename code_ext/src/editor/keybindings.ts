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

import { CONFIG_SECTION } from "../connections/settings.js";

/**
 * Keyboard shortcuts cannot be built from a setting: `contributes.keybindings`
 * is static. What can be done is to contribute one binding per (command,
 * chord) pair, each gated on a `when` clause that names a context key, and
 * then set those context keys from the settings. That is what this does -
 * so the two run commands can be moved between a fixed set of chords from
 * the extension's own settings, while the Keyboard Shortcuts editor still
 * works for anything outside that set.
 */

/** The chords a run command can be bound to. */
export const CHORDS = [
    "cmdOrCtrl+enter",
    "shift+enter",
    "cmdOrCtrl+shift+enter",
    "alt+enter",
    "f5",
    "f8",
    "none",
] as const;

export type Chord = (typeof CHORDS)[number];

/** The commands whose chord can be chosen. */
export const BINDABLE = {
    runScript: {
        setting: "keybindings.runScript",
        contextKey: "mariadb.chord.runScript",
        fallback: "cmdOrCtrl+enter" as Chord,
    },
    runStatement: {
        setting: "keybindings.runStatementAtCursor",
        contextKey: "mariadb.chord.runStatement",
        fallback: "shift+enter" as Chord,
    },
} as const;

export type Bindable = keyof typeof BINDABLE;

/**
 * Reads the chord chosen for a command.
 *
 * @param what The command to look up.
 * @param read Reads a setting; the configuration by default.
 *
 * @returns The chord, or its fallback when the setting holds something
 *          this version does not offer.
 */
export const chordFor = (
    what: Bindable,
    read: (setting: string) => string | undefined = (setting) => {
        return vscode.workspace
            .getConfiguration(CONFIG_SECTION)
            .get<string>(setting);
    },
): Chord => {
    const { setting, fallback } = BINDABLE[what];
    const value = read(setting);

    return (CHORDS as readonly string[]).includes(value ?? "")
        ? value as Chord
        : fallback;
};

/**
 * Publishes the chosen chords as context keys, which is what the
 * contributed keybindings switch on.
 *
 * @param setContext Sets a context key; VS Code's command by default.
 *
 * @returns Nothing.
 */
export const applyKeybindings = async (
    setContext: (key: string, value: string) => Thenable<unknown> =
    (key, value) => {
        return vscode.commands.executeCommand("setContext", key, value);
    },
): Promise<void> => {
    for (const what of Object.keys(BINDABLE) as Bindable[]) {
        await setContext(BINDABLE[what].contextKey, chordFor(what));
    }
};
