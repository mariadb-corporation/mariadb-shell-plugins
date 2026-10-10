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

import { appendFile, mkdir, readdir, rm } from "node:fs/promises";
import { join } from "node:path";

import { isFinished, type IUtilTaskMessage } from "../mcp/utilApi.js";
import type { ITaskReport, ITrackedTask } from "./taskMonitor.js";

/**
 * Each task's own log, as a file: what the shell reported for it, in order,
 * from the call that started it to how it ended. It is written as the
 * messages come, so it can be watched while the task runs, and it outlives
 * the window - the output channel and the server's messages do not. A log
 * goes when its task leaves the Tasks view.
 */

/**
 * @param message A message of a task.
 *
 * @returns Its line in the log: time, level where it matters, text.
 */
export const logLine = (message: IUtilTaskMessage): string => {
    const level = message.level === "warning" || message.level === "error"
        ? `${message.level.toUpperCase()}: ` : "";

    return `${message.time} ${level}${message.text}`;
};

/**
 * @param tracked A task that ended.
 *
 * @returns The log's last line, saying how.
 */
export const outcomeLine = (tracked: ITrackedTask): string => {
    const { state } = tracked;
    const when = state.finished_at ?? new Date().toISOString();
    switch (state.status) {
        case "completed": {
            return `${when} Completed.`;
        }

        case "cancelled": {
            return `${when} Cancelled.`;
        }

        default: {
            return `${when} FAILED: ${state.error ?? "no reason given"}`;
        }
    }
};

/**
 * @param tracked A task that is starting.
 *
 * @returns The log's first lines: what it is, where, and the call.
 */
export const headerLines = (tracked: ITrackedTask): string[] => {
    return [
        tracked.state.title,
        `Connection: ${tracked.connection}`,
        ...(tracked.call === undefined ? [] : [`Call: ${tracked.call}`]),
        `Started: ${tracked.state.created_at}`,
        "",
    ];
};

/** The task logs in one folder, a file per task. */
export class TaskLogs {
    /** Writes in the order they were asked for, one task at a time. */
    #writing: Promise<void> = Promise.resolve();

    /**
     * @param folder Where the logs go: the extension's own storage.
     * @param log Where a log that could not be written is told.
     */
    public constructor(
        private readonly folder: string,
        private readonly log: (message: string) => void,
    ) { }

    /**
     * @param taskId A task.
     *
     * @returns Where its log is.
     */
    public pathOf(taskId: string): string {
        return join(this.folder, `${taskId.replace(/[^\w-]/gu, "_")}.log`);
    }

    /**
     * Writes what a look at a task brought.
     *
     * @param report The task and its new messages.
     *
     * @returns Once it is written.
     */
    public async report(report: ITaskReport): Promise<void> {
        const { tracked, messages } = report;
        const lines = [
            ...(report.first ? headerLines(tracked) : []),
            ...messages.map(logLine),
        ];
        if (isFinished(tracked.state.status)) {
            lines.push(outcomeLine(tracked));
        }
        if (lines.length === 0) {
            return;
        }

        await this.#queue(async () => {
            await mkdir(this.folder, { recursive: true });
            await appendFile(this.pathOf(tracked.state.task_id),
                `${lines.join("\n")}\n`);
        });
    }

    /**
     * Deletes the logs of the tasks no longer listed.
     *
     * @param kept The ids of the tasks still listed.
     *
     * @returns Once they are gone.
     */
    public async prune(kept: Iterable<string>): Promise<void> {
        const keep = new Set([...kept].map((taskId) => {
            return this.pathOf(taskId);
        }));

        await this.#queue(async () => {
            let names: string[];
            try {
                names = await readdir(this.folder);
            } catch {
                return;
            }
            for (const name of names) {
                const path = join(this.folder, name);
                if (name.endsWith(".log") && !keep.has(path)) {
                    await rm(path, { force: true });
                }
            }
        });
    }

    #queue(work: () => Promise<void>): Promise<void> {
        const next = this.#writing.then(work).catch((error: unknown) => {
            this.log(`A task log could not be written: ${String(error)}`);
        });
        this.#writing = next;

        return next;
    }
}
