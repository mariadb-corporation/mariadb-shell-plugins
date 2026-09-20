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

import { beforeEach, describe, expect, it } from "vitest";

import {
    applyKeybindings,
    BINDABLE,
    CHORDS,
    chordFor,
} from "../../editor/keybindings.js";
import { configuration, resetVscodeMock } from "../mocks/vscode.js";

describe("chordFor", () => {
    it("defaults to cmd/ctrl+enter for the script, shift+enter for the "
        + "statement", () => {
        const empty = (): undefined => {
            return undefined;
        };

        expect(chordFor("runScript", empty)).toBe("cmdOrCtrl+enter");
        expect(chordFor("runStatement", empty)).toBe("shift+enter");
    });

    it("takes the chord from the setting", () => {
        expect(chordFor("runScript", () => {
            return "f5";
        })).toBe("f5");
    });

    it("falls back when the setting holds something unknown", () => {
        // A setting written by a newer version, or by hand.
        expect(chordFor("runScript", () => {
            return "ctrl+alt+shift+j";
        })).toBe("cmdOrCtrl+enter");
    });

    it("accepts every chord it offers", () => {
        for (const chord of CHORDS) {
            expect(chordFor("runStatement", () => {
                return chord;
            })).toBe(chord);
        }
    });
});

describe("applyKeybindings", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("publishes the chords as context keys", async () => {
        const set = new Map<string, string>();

        await applyKeybindings((key, value) => {
            set.set(key, value);

            return Promise.resolve(undefined);
        });

        // These are what the contributed keybindings switch on: there is
        // no other way for a setting to move a keyboard shortcut.
        expect(set.get(BINDABLE.runScript.contextKey))
            .toBe("cmdOrCtrl+enter");
        expect(set.get(BINDABLE.runStatement.contextKey))
            .toBe("shift+enter");
    });

    it("follows the settings", async () => {
        configuration.set("mariadb.keybindings.runScript", "f5");
        configuration.set(
            "mariadb.keybindings.runStatementAtCursor", "f8");
        const set = new Map<string, string>();

        await applyKeybindings((key, value) => {
            set.set(key, value);

            return Promise.resolve(undefined);
        });

        expect(set.get(BINDABLE.runScript.contextKey)).toBe("f5");
        expect(set.get(BINDABLE.runStatement.contextKey)).toBe("f8");
    });
});

describe("the contributed keybindings", () => {
    it("cover every chord for both commands", async () => {
        // Each (command, chord) pair needs its own contributed binding,
        // gated on the context key, or a setting could name a chord that
        // nothing is bound to.
        const manifest = await import("../../../package.json", {
            with: { type: "json" },
        }) as unknown as {
            default: {
                contributes: {
                    keybindings: Array<{
                        command: string;
                        when: string;
                    }>;
                };
            };
        };
        const bindings = manifest.default.contributes.keybindings;

        for (const [what, { contextKey }] of Object.entries(BINDABLE)) {
            const command = what === "runScript"
                ? "mariadb.runSqlFile"
                : "mariadb.runSqlStatement";

            for (const chord of CHORDS) {
                if (chord === "none") {
                    continue;
                }

                const found = bindings.some((binding) => {
                    return binding.command === command
                        && binding.when.includes(
                            `${contextKey} == '${chord}'`);
                });
                expect(found, `${command} has no binding for ${chord}`)
                    .toBe(true);
            }
        }
    });
});
