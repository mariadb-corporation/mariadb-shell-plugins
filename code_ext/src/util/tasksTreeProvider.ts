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

import { isFinished } from "../mcp/utilApi.js";
import type { ITrackedTask, TaskMonitor } from "./taskMonitor.js";

export const TASKS_VIEW_ID = "mariadb.tasks";

const ICONS: Record<string, string> = {
    pending: "loading~spin",
    running: "sync~spin",
    completed: "pass",
    failed: "error",
    cancelled: "circle-slash",
};

/**
 * @param tracked A task.
 *
 * @returns What its row says after the title: the state, and while it runs
 *          the stage and how far it got.
 */
export const taskDescription = (tracked: ITrackedTask): string => {
    const { state } = tracked;
    if (isFinished(state.status)) {
        return state.status;
    }
    if (tracked.cancelling) {
        return "cancelling...";
    }

    const percent = state.progress?.percent;
    const parts = [state.stage ?? state.status];
    if (typeof percent === "number" && state.progress?.items !== undefined) {
        parts.push(`${String(Math.floor(percent))}%`);
    }

    return parts.join(" ");
};

/**
 * @param tracked A task.
 *
 * @returns Its tooltip: the connection, the stages and how it ended.
 */
export const taskTooltip = (tracked: ITrackedTask): string => {
    const { state } = tracked;
    const lines = [state.title, tracked.connection];
    for (const stage of state.stages) {
        const seconds = stage.seconds === null ? ""
            : ` (${stage.seconds.toFixed(1)} s)`;
        lines.push(`${stage.status === "completed" ? "✓" : "•"} ${stage.name}`
            + seconds);
    }
    if (state.error !== null) {
        lines.push(state.error);
    }

    return lines.join("\n");
};

export class TaskTreeItem extends vscode.TreeItem {
    public constructor(public readonly tracked: ITrackedTask) {
        super(tracked.state.title, vscode.TreeItemCollapsibleState.None);

        this.id = tracked.state.task_id;
        this.description = taskDescription(tracked);
        this.tooltip = taskTooltip(tracked);
        this.iconPath = new vscode.ThemeIcon(
            ICONS[tracked.state.status] ?? "circle-outline");
        this.contextValue = isFinished(tracked.state.status)
            ? "mariadbTask.finished" : "mariadbTask.running";
        this.command = {
            command: "mariadb.showTaskOutput",
            title: "Show Task Output",
        };
    }
}

/** The Tasks view: the dumps, loads and copies started in this window. */
export class TasksTreeProvider implements vscode.TreeDataProvider<ITrackedTask> {
    #changed = new vscode.EventEmitter<ITrackedTask | undefined>();

    public readonly onDidChangeTreeData = this.#changed.event;

    public constructor(private readonly monitor: TaskMonitor) {
        // The whole list: rows are few, and one that ended moves state.
        monitor.onDidChange(() => { this.#changed.fire(undefined); });
    }

    public getTreeItem(element: ITrackedTask): vscode.TreeItem {
        return new TaskTreeItem(element);
    }

    public getChildren(element?: ITrackedTask): ITrackedTask[] {
        return element === undefined ? this.monitor.tasks : [];
    }

    public dispose(): void {
        this.#changed.dispose();
    }
}
