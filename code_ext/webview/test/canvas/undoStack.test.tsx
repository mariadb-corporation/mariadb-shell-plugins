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

import { describe, expect, it } from "vitest";

import { UndoStack, type IUndoableAction } from "../../src/canvas/undoStack.js";

/** An action that sets a value, and the log of what it did. */
const setter = (
    state: { value: number },
    from: number,
    to: number,
): IUndoableAction => {
    return {
        label: `Set ${to}`,
        undo: () => { state.value = from; },
        redo: () => { state.value = to; },
    };
};

describe("UndoStack", () => {
    it("executes, undoes and redoes in order", () => {
        const state = { value: 0 };
        const stack = new UndoStack();
        stack.execute(setter(state, 0, 1));
        stack.execute(setter(state, 1, 2));

        expect(state.value).toBe(2);
        expect(stack.undo()?.label).toBe("Set 2");
        expect(state.value).toBe(1);
        expect(stack.undo()?.label).toBe("Set 1");
        expect(state.value).toBe(0);
        expect(stack.undo()).toBeUndefined();
        expect(stack.canUndo).toBe(false);

        expect(stack.redo()?.label).toBe("Set 1");
        expect(stack.redo()?.label).toBe("Set 2");
        expect(state.value).toBe(2);
        expect(stack.redo()).toBeUndefined();
        expect(stack.canRedo).toBe(false);
    });

    it("records an action already made without making it again", () => {
        const state = { value: 5 };
        const stack = new UndoStack();
        stack.record(setter(state, 0, 9));

        expect(state.value).toBe(5);
        stack.undo();
        expect(state.value).toBe(0);
    });

    it("drops what was undone when a new action comes", () => {
        const state = { value: 0 };
        const stack = new UndoStack();
        stack.execute(setter(state, 0, 1));
        stack.execute(setter(state, 1, 2));
        stack.undo();

        expect(stack.entries).toEqual([
            { label: "Set 1", done: true },
            { label: "Set 2", done: false },
        ]);

        stack.execute(setter(state, 1, 3));
        expect(stack.entries).toEqual([
            { label: "Set 1", done: true },
            { label: "Set 3", done: true },
        ]);
        expect(stack.canRedo).toBe(false);
    });

    it("keeps no more than its limit, the oldest going first", () => {
        const state = { value: 0 };
        const stack = new UndoStack(2);
        stack.execute(setter(state, 0, 1));
        stack.execute(setter(state, 1, 2));
        stack.execute(setter(state, 2, 3));

        expect(stack.entries.map((entry) => { return entry.label; }))
            .toEqual(["Set 2", "Set 3"]);
        stack.undo();
        stack.undo();
        expect(state.value).toBe(1);
        expect(stack.canUndo).toBe(false);
    });

    it("tells its listeners of every change", () => {
        const state = { value: 0 };
        const stack = new UndoStack();
        let changes = 0;
        const stop = stack.onChange(() => { changes += 1; });

        stack.execute(setter(state, 0, 1));
        stack.undo();
        stack.redo();
        // Nothing to redo: no change.
        stack.redo();
        stack.clear();
        expect(changes).toBe(4);
        expect(stack.entries).toEqual([]);

        stop();
        stack.execute(setter(state, 0, 1));
        expect(changes).toBe(4);
    });
});
