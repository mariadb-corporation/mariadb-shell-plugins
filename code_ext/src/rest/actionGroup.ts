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

import { formatTime } from "../sql/executionService.js";
import { errorText } from "../text.js";
import type { ActionPutter } from "../sandboxes/sandboxActivity.js";
import type { IActionRow } from "../webview/protocol.js";

export type { ActionPutter };

/** What a step, or the whole, is called while it runs and once done. */
export interface IStepWords {
    /** "Creating Sandbox on port 3310", shown with an ellipsis. */
    doing: string;
    /** "Created Sandbox on port 3310". */
    done: string;
}

/** What a step came to, from its result, and the log a failure names. */
export interface IStepOptions<T> {
    detail?: (value: T) => string | undefined;
    logOf?: (error: unknown) => Promise<string | undefined>;
}

/** One step, as its row shows it. */
interface IStep {
    id: string;
    words: IStepWords;
    /** The call it makes, the row's Information. */
    call: string;
    when: Date;
    /** What it came to, in place of the call once done. */
    detail?: string;
    error?: string;
    logPath?: string;
    elapsedMs?: number;
}

/** Makes the groups' ids unique. */
let groups = 0;

/**
 * Several steps reported as one action of the Actions tab: a row for the
 * whole, with a row for each step under it, as a SQL script's run has a
 * row for each statement. The whole has the spinner while any step
 * runs, and ends as the steps did: done, or failed with the first error
 * to jump to.
 *
 * A step that fails can name a log, which its row offers to open.
 */
export class ActionGroup {
    readonly #id: string;
    readonly #when = new Date();
    readonly #steps: IStep[] = [];
    #finished?: { summary?: string };

    /**
     * @param put Where the rows go up.
     * @param connection The connection they are filed under.
     * @param words What the whole is called.
     */
    public constructor(
        private readonly put: ActionPutter,
        private readonly connection: string,
        private readonly words: IStepWords,
    ) {
        groups += 1;
        this.#id = `group-${String(groups)}`;
        this.#show();
    }

    /**
     * Runs one step, its row pending while it does.
     *
     * @param words What it is called.
     * @param call The call it makes, as its row shows it.
     * @param work The step.
     * @param options What it came to, from its result, and the log a
     *                failure names.
     *
     * @returns What the step returned.
     */
    public async step<T>(
        words: IStepWords,
        call: string,
        work: () => Promise<T>,
        options: IStepOptions<T> = {},
    ): Promise<T> {
        const step: IStep = {
            id: `${this.#id}-${String(this.#steps.length + 1)}`,
            words, call, when: new Date(),
        };
        this.#steps.push(step);
        this.#show();

        try {
            const value = await work();
            step.elapsedMs = Date.now() - step.when.getTime();
            const detail = options.detail?.(value);
            if (detail !== undefined) {
                step.detail = detail;
            }
            this.#show();

            return value;
        } catch (error) {
            step.elapsedMs = Date.now() - step.when.getTime();
            step.error = errorText(error);
            const logPath = await options.logOf?.(error).catch(() => {
                return undefined;
            });
            if (logPath !== undefined) {
                step.logPath = logPath;
            }
            this.#show();
            throw error;
        }
    }

    /**
     * Ends the whole: done where no step failed, failed where one did.
     *
     * @param summary What it came to, for its Information column.
     */
    public finish(summary?: string): void {
        this.#finished = summary === undefined ? {} : { summary };
        this.#show();
    }

    /** Puts the rows up as they are now. */
    #show(): void {
        const base = { connection: this.connection, connectionLabel: "" };
        const children = this.#steps.map((step): IActionRow => {
            const stepPending = step.elapsedMs === undefined;

            return {
                ...base,
                id: step.id,
                time: formatTime(step.when),
                role: "statement",
                statement: step.error === undefined
                    ? step.detail ?? step.call : step.call,
                message: stepPending
                    ? `${step.words.doing}…`
                    : step.error === undefined
                        ? step.words.done
                        : `${step.words.doing} failed: ${step.error}`,
                kind: stepPending ? "pending"
                    : step.error === undefined ? "info" : "error",
                ...(step.elapsedMs === undefined
                    ? {} : { elapsedMs: step.elapsedMs }),
                ...(step.logPath === undefined
                    ? {} : { logPath: step.logPath }),
            };
        });
        const failedStep = this.#steps.find((step) => {
            return step.error !== undefined;
        });
        const pending = this.#finished === undefined
            || this.#steps.some((step) => {
                return step.elapsedMs === undefined;
            });
        const errors = this.#steps.filter((step) => {
            return step.error !== undefined;
        }).length;

        this.put(this.connection, {
            ...base,
            id: this.#id,
            time: formatTime(this.#when),
            role: "run",
            statement: "",
            message: pending
                ? `${this.words.doing}…`
                : failedStep === undefined
                    ? this.words.done
                    : `${this.words.doing} failed`,
            kind: pending ? "pending"
                : failedStep === undefined ? "info" : "error",
            children,
            ...(pending ? {} : {
                summary: failedStep === undefined
                    ? this.#finished?.summary ?? ""
                    : `Finished with ${String(errors)} `
                    + (errors === 1 ? "error" : "errors"),
                elapsedMs: Date.now() - this.#when.getTime(),
            }),
            ...(failedStep === undefined
                ? {} : { jumpToRowId: failedStep.id }),
            ...(failedStep?.logPath === undefined
                ? {} : { logPath: failedStep.logPath }),
        });
    }
}
