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

import type { IToolCaller } from "./mariaDbApi.js";
import { decodeList, decodeObject } from "./protocol.js";

/**
 * The `util.*` tools: the shell's dump, load, copy, export and import
 * utilities, each run by the server as a background task. A start call
 * answers at once with the task's id; `getTask` follows it.
 */

export type UtilTaskStatus =
    | "pending" | "running" | "completed" | "failed" | "cancelled";

/** The utilities a task can run, as the server names them. */
export type UtilTaskKind =
    | "dump_instance" | "dump_schemas" | "dump_tables" | "export_table"
    | "load_dump" | "import_table"
    | "copy_instance" | "copy_schemas" | "copy_tables";

/** The progress of a task's current stage. */
export interface IUtilTaskProgress {
    current: number;
    total: number;
    /** 0 to 100, or null where the total is not known yet. */
    percent: number | null;
    /** Items per second, for a stage that measures throughput. */
    throughput?: number;
    eta_seconds?: number;
    /** What is counted: `bytes` or `rows`. */
    items?: string;
    total_is_approximate?: boolean;
    total_known?: boolean;
}

export interface IUtilTaskStage {
    name: string;
    status: UtilTaskStatus;
    /** How long it took, once it finished. */
    seconds: number | null;
}

export interface IUtilTaskMessage {
    seq: number;
    time: string;
    /** output, info, status, note, warning, error or diag. */
    level: string;
    text: string;
}

/** A task as `util.get_task` reports it. */
export interface IUtilTaskState {
    task_id: string;
    kind: UtilTaskKind;
    title: string;
    connection_id: string | null;
    status: UtilTaskStatus;
    cancel_requested: boolean;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    stage: string | null;
    progress: IUtilTaskProgress | null;
    stages: IUtilTaskStage[];
    /** The messages after the `since` asked for; none from `listTasks`. */
    messages?: IUtilTaskMessage[];
    messages_dropped?: number;
    /** The `since` of the next call. */
    next_since: number;
    result: Record<string, unknown> | null;
    error: string | null;
}

export interface IUtilTaskStarted {
    task_id: string;
    status: UtilTaskStatus;
}

/** Options of a utility, with the shell's camelCase names. */
export type UtilOptions = Record<string, unknown>;

/** How long a start call may take: it opens a session first. */
export const START_TIMEOUT_MS = 2 * 60 * 1000;

/** How long `getTask` asks the server to wait for a change. */
export const WAIT_MS = 2000;

export const isFinished = (status: UtilTaskStatus): boolean => {
    return status === "completed" || status === "failed"
        || status === "cancelled";
};

export interface IUtilApi {
    dumpInstance(connectionId: string, outputUrl: string,
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    dumpSchemas(connectionId: string, schemas: string[], outputUrl: string,
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    dumpTables(connectionId: string, schema: string, tables: string[],
        outputUrl: string, options?: UtilOptions): Promise<IUtilTaskStarted>;
    exportTable(connectionId: string, table: string, outputUrl: string,
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    loadDump(connectionId: string, url: string,
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    importTable(connectionId: string, urls: string | string[],
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    copyInstance(connectionId: string, targetConnectionId: string,
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    copySchemas(connectionId: string, schemas: string[],
        targetConnectionId: string,
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    copyTables(connectionId: string, schema: string, tables: string[],
        targetConnectionId: string,
        options?: UtilOptions): Promise<IUtilTaskStarted>;
    getTask(taskId: string, since?: number,
        waitMs?: number): Promise<IUtilTaskState>;
    listTasks(): Promise<IUtilTaskState[]>;
    cancelTask(taskId: string): Promise<IUtilTaskState>;
}

export class UtilApi implements IUtilApi {
    public constructor(private readonly caller: IToolCaller) { }

    public async dumpInstance(connectionId: string, outputUrl: string,
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.dump_instance", {
            connection_id: connectionId, output_url: outputUrl,
        }, options);
    }

    public async dumpSchemas(connectionId: string, schemas: string[],
        outputUrl: string, options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.dump_schemas", {
            connection_id: connectionId, schemas, output_url: outputUrl,
        }, options);
    }

    public async dumpTables(connectionId: string, schema: string,
        tables: string[], outputUrl: string,
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.dump_tables", {
            connection_id: connectionId, schema, tables, output_url: outputUrl,
        }, options);
    }

    public async exportTable(connectionId: string, table: string,
        outputUrl: string, options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.export_table", {
            connection_id: connectionId, table, output_url: outputUrl,
        }, options);
    }

    public async loadDump(connectionId: string, url: string,
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.load_dump", {
            connection_id: connectionId, url,
        }, options);
    }

    public async importTable(connectionId: string, urls: string | string[],
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.import_table", {
            connection_id: connectionId, urls,
        }, options);
    }

    public async copyInstance(connectionId: string, targetConnectionId: string,
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.copy_instance", {
            connection_id: connectionId,
            target_connection_id: targetConnectionId,
        }, options);
    }

    public async copySchemas(connectionId: string, schemas: string[],
        targetConnectionId: string,
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.copy_schemas", {
            connection_id: connectionId, schemas,
            target_connection_id: targetConnectionId,
        }, options);
    }

    public async copyTables(connectionId: string, schema: string,
        tables: string[], targetConnectionId: string,
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        return await this.#start("util.copy_tables", {
            connection_id: connectionId, schema, tables,
            target_connection_id: targetConnectionId,
        }, options);
    }

    public async getTask(taskId: string, since = 0,
        waitMs = 0): Promise<IUtilTaskState> {
        const name = "util.get_task";
        // Longer than the server's own wait, which is capped at 30 seconds.
        return decodeObject<IUtilTaskState>(name, await this.caller.callTool(
            name, { task_id: taskId, since, wait_ms: waitMs },
            waitMs + 30_000));
    }

    public async listTasks(): Promise<IUtilTaskState[]> {
        const name = "util.list_tasks";
        return decodeList<IUtilTaskState>(name,
            await this.caller.callTool(name, {}));
    }

    public async cancelTask(taskId: string): Promise<IUtilTaskState> {
        const name = "util.cancel_task";
        return decodeObject<IUtilTaskState>(name,
            await this.caller.callTool(name, { task_id: taskId }));
    }

    async #start(name: string, args: Record<string, unknown>,
        options?: UtilOptions): Promise<IUtilTaskStarted> {
        const withOptions = options === undefined
            || Object.keys(options).length === 0
            ? args : { ...args, options };

        return decodeObject<IUtilTaskStarted>(name,
            await this.caller.callTool(name, withOptions, START_TIMEOUT_MS));
    }
}
