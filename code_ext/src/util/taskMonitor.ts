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

import { errorText } from "../text.js";
import {
    isFinished,
    WAIT_MS,
    type IUtilApi,
    type IUtilTaskMessage,
    type IUtilTaskStarted,
    type IUtilTaskState,
} from "../mcp/utilApi.js";

/** A task the extension started, as the Tasks view and the commands see it. */
export interface ITrackedTask {
    state: IUtilTaskState;
    /** The connection it works on, by address. */
    connection: string;
    /** Whether Cancel was asked for and not answered yet. */
    cancelling: boolean;
}

export interface IFollowOptions {
    title: string;
    connection: string;
    /** Called once it ended, whichever way. */
    onDone?(state: IUtilTaskState): void;
}

/** Where messages of the tasks go, and the notifications about them. */
export interface ITaskOutput {
    appendLine(line: string): void;
    show(preserveFocus?: boolean): void;
}

/**
 * Follows the server's background tasks: asks for each task's state until it
 * ended, writes its messages to the output channel, and shows its progress
 * in a notification that can cancel it.
 *
 * A task survives a closed notification: it goes on in the server, and the
 * Tasks view still shows it.
 */
export class TaskMonitor {
    #tasks = new Map<string, ITrackedTask>();
    #changed = new vscode.EventEmitter<ITrackedTask | undefined>();

    public readonly onDidChange = this.#changed.event;

    public constructor(
        private readonly api: () => Promise<IUtilApi>,
        private readonly output: ITaskOutput,
        private readonly log: (message: string) => void,
    ) { }

    /**
     * @returns The tasks started in this window, the newest first.
     */
    public get tasks(): ITrackedTask[] {
        return [...this.#tasks.values()].reverse();
    }

    public task(taskId: string): ITrackedTask | undefined {
        return this.#tasks.get(taskId);
    }

    /**
     * Follows a task just started, with a progress notification, until it
     * ends.
     *
     * @param started What the start call answered.
     * @param options What it is.
     *
     * @returns Its final state.
     */
    public async follow(
        started: IUtilTaskStarted,
        options: IFollowOptions,
    ): Promise<IUtilTaskState> {
        const tracked: ITrackedTask = {
            connection: options.connection,
            cancelling: false,
            state: {
                task_id: started.task_id, kind: "dump_instance",
                title: options.title, connection_id: null,
                status: started.status, cancel_requested: false,
                created_at: new Date().toISOString(), started_at: null,
                finished_at: null, stage: null, progress: null, stages: [],
                next_since: 0, result: null, error: null,
            },
        };
        this.#tasks.set(started.task_id, tracked);
        this.#changed.fire(tracked);
        this.output.appendLine(`[${options.title}] Started.`);

        const state = await vscode.window.withProgress({
            location: vscode.ProgressLocation.Notification,
            title: options.title,
            cancellable: true,
        }, async (progress, token) => {
            token.onCancellationRequested(() => {
                void this.cancel(started.task_id);
            });

            let reported = 0;

            return await this.#poll(tracked, options.title, (current) => {
                if (isFinished(current.status)) {
                    // the notification closes now; the outcome is told apart
                    return;
                }
                const percent = current.progress?.percent;
                const stage = current.stage ?? "Starting";
                // The bar follows the stages that move data: the others are
                // quick, and their counts are of other things.
                const measured = current.progress?.items !== undefined
                    && typeof percent === "number";
                const increment = measured ? Math.max(0, percent - reported) : 0;
                if (measured && increment > 0) {
                    reported = percent;
                }
                progress.report({
                    message: measured ? `${stage}: ${Math.floor(percent)}%`
                        : stage,
                    ...(increment > 0 ? { increment } : {}),
                });
            });
        });

        this.#report(state, options.title);
        options.onDone?.(state);

        return state;
    }

    /**
     * Asks the server to stop a task.
     *
     * @param taskId The task.
     */
    public async cancel(taskId: string): Promise<void> {
        const tracked = this.#tasks.get(taskId);
        if (tracked === undefined || isFinished(tracked.state.status)) {
            return;
        }

        tracked.cancelling = true;
        this.#changed.fire(tracked);
        try {
            await (await this.api()).cancelTask(taskId);
            this.output.appendLine(`[${tracked.state.title}] Cancelling...`);
        } catch (error) {
            tracked.cancelling = false;
            this.#changed.fire(tracked);
            void vscode.window.showErrorMessage(
                `Could not cancel ${tracked.state.title}: ${errorText(error)}`);
        }
    }

    /**
     * Takes a finished task off the list.
     *
     * @param taskId The task.
     */
    public remove(taskId: string): void {
        const tracked = this.#tasks.get(taskId);
        if (tracked !== undefined && isFinished(tracked.state.status)) {
            this.#tasks.delete(taskId);
            this.#changed.fire(undefined);
        }
    }

    /** Takes every finished task off the list. */
    public clearFinished(): void {
        for (const [taskId, tracked] of this.#tasks) {
            if (isFinished(tracked.state.status)) {
                this.#tasks.delete(taskId);
            }
        }
        this.#changed.fire(undefined);
    }

    public showOutput(): void {
        this.output.show(true);
    }

    async #poll(
        tracked: ITrackedTask,
        title: string,
        onUpdate: (state: IUtilTaskState) => void,
    ): Promise<IUtilTaskState> {
        let since = 0;

        for (;;) {
            let state: IUtilTaskState;
            try {
                state = await (await this.api()).getTask(
                    tracked.state.task_id, since, WAIT_MS);
            } catch (error) {
                // The server went away, and the task with it, or it forgot
                // the task: either way, there is nothing more to follow.
                const text = errorText(error);
                this.log(`Lost track of the task ${title}: ${text}`);
                state = {
                    ...tracked.state,
                    status: "failed",
                    error: `Lost track of the task: ${text}`,
                    finished_at: new Date().toISOString(),
                };
            }

            for (const message of state.messages ?? []) {
                this.output.appendLine(this.#line(title, message));
            }
            if ((state.messages_dropped ?? 0) > 0) {
                this.output.appendLine(`[${title}] (${String(
                    state.messages_dropped)} messages were dropped)`);
            }
            since = state.next_since;

            tracked.state = { ...state, messages: [] };
            if (state.cancel_requested || isFinished(state.status)) {
                tracked.cancelling = !isFinished(state.status);
            }
            this.#changed.fire(tracked);
            onUpdate(state);

            if (isFinished(state.status)) {
                return state;
            }
        }
    }

    #line(title: string, message: IUtilTaskMessage): string {
        const level = message.level === "warning" || message.level === "error"
            ? `${message.level.toUpperCase()}: ` : "";

        return `[${title}] ${level}${message.text}`;
    }

    #report(state: IUtilTaskState, title: string): void {
        switch (state.status) {
            case "completed": {
                this.output.appendLine(`[${title}] Completed.`);
                void vscode.window.showInformationMessage(
                    `${title} completed.`);
                break;
            }

            case "cancelled": {
                this.output.appendLine(`[${title}] Cancelled.`);
                void vscode.window.showWarningMessage(`${title} was cancelled.`);
                break;
            }

            default: {
                this.output.appendLine(`[${title}] Failed: ${String(
                    state.error)}`);
                void vscode.window.showErrorMessage(
                    `${title} failed: ${String(state.error)}`, "Show Output")
                    .then((answer) => {
                        if (answer === "Show Output") {
                            this.output.show(true);
                        }
                    });
            }
        }
    }

    public dispose(): void {
        this.#changed.dispose();
    }
}
