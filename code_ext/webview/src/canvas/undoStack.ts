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
 * The diagram's history: every change as an action that can be undone and
 * redone, in the order it was made.
 *
 * The stack keeps the actions undone too, until a new action replaces
 * them, so a list of all actions can show what Redo would bring back.
 * Pure, so it is tested without a scene.
 */

/** A change that can be taken back and made again. */
export interface IUndoableAction {
    /** What the action did, for a menu or a list: "Move service". */
    readonly label: string;
    /** Takes the change back. */
    undo(): void;
    /** Makes the change (again). */
    redo(): void;
}

/** One action as a list of the history shows it. */
export interface IHistoryEntry {
    label: string;
    /** Whether the action is in effect; false for one undone. */
    done: boolean;
}

/** How many actions the history keeps by default. */
const DEFAULT_HISTORY_LIMIT = 500;

export class UndoStack {
    readonly #limit: number;
    #actions: IUndoableAction[] = [];
    /** How many of the actions, from the start, are in effect. */
    #done = 0;
    #listeners = new Set<() => void>();

    /**
     * @param limit How many actions are kept; the oldest go first.
     */
    public constructor(limit = DEFAULT_HISTORY_LIMIT) {
        this.#limit = Math.max(1, limit);
    }

    /**
     * Records an action that has already been made - a drag, done as the
     * pointer moved. The actions undone so far are dropped: they can no
     * longer be redone after a new change.
     *
     * @param action The action.
     *
     * @returns Nothing.
     */
    public record(action: IUndoableAction): void {
        this.#actions.splice(this.#done);
        this.#actions.push(action);
        if (this.#actions.length > this.#limit) {
            this.#actions.splice(0, this.#actions.length - this.#limit);
        }
        this.#done = this.#actions.length;
        this.#changed();
    }

    /**
     * Makes an action, then records it.
     *
     * @param action The action.
     *
     * @returns Nothing.
     */
    public execute(action: IUndoableAction): void {
        action.redo();
        this.record(action);
    }

    public get canUndo(): boolean {
        return this.#done > 0;
    }

    public get canRedo(): boolean {
        return this.#done < this.#actions.length;
    }

    /**
     * Takes the last action in effect back.
     *
     * @returns The action undone, or nothing where there is none.
     */
    public undo(): IUndoableAction | undefined {
        if (!this.canUndo) {
            return undefined;
        }
        this.#done -= 1;
        const action = this.#actions[this.#done];
        action.undo();
        this.#changed();

        return action;
    }

    /**
     * Makes the first action undone again.
     *
     * @returns The action redone, or nothing where there is none.
     */
    public redo(): IUndoableAction | undefined {
        if (!this.canRedo) {
            return undefined;
        }
        const action = this.#actions[this.#done];
        this.#done += 1;
        action.redo();
        this.#changed();

        return action;
    }

    /** @returns Every action kept, oldest first. */
    public get entries(): IHistoryEntry[] {
        return this.#actions.map((action, index) => {
            return { label: action.label, done: index < this.#done };
        });
    }

    /**
     * Forgets every action.
     *
     * @returns Nothing.
     */
    public clear(): void {
        this.#actions = [];
        this.#done = 0;
        this.#changed();
    }

    /**
     * Listens for changes: an action recorded, undone or redone, or the
     * history cleared.
     *
     * @param listener Called after each change.
     *
     * @returns Stops listening.
     */
    public onChange(listener: () => void): () => void {
        this.#listeners.add(listener);

        return () => { this.#listeners.delete(listener); };
    }

    #changed(): void {
        for (const listener of this.#listeners) {
            listener();
        }
    }
}
