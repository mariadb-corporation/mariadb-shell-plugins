/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

/**
 * A minimal stand-in for the `vscode` module. The extension host provides
 * the real one, which cannot be imported from a plain Node process, so the
 * vitest config aliases `vscode` onto this file.
 *
 * Only what the extension actually touches is modelled here, and every call
 * is recorded so tests can assert on it.
 */

export enum ProgressLocation {
    SourceControl = 1,
    Window = 10,
    Notification = 15,
}

export interface Progress<T> {
    report(value: T): void;
}

export interface ProgressOptions {
    location: ProgressLocation;
    title?: string;
    cancellable?: boolean;
}

export interface CancellationToken {
    isCancellationRequested: boolean;
}

export interface WithProgressCall {
    options: ProgressOptions;
    reported: string[];
}

/** Every `withProgress` call made since the last `resetVscodeMock()`. */
export const withProgressCalls: WithProgressCall[] = [];
export const informationMessages: string[] = [];
export const errorMessages: string[] = [];
export const outputChannels: MockOutputChannel[] = [];
export const registeredCommands = new Map<
    string,
    (...args: unknown[]) => unknown
>();

export class MockOutputChannel {
    public readonly lines: string[] = [];
    public shown = false;
    public disposed = false;

    public constructor(public readonly name: string) { }

    public appendLine(line: string): void {
        this.lines.push(line);
    }

    public show(_preserveFocus?: boolean): void {
        this.shown = true;
    }

    public dispose(): void {
        this.disposed = true;
    }
}

export const window = {
    createOutputChannel: (name: string): MockOutputChannel => {
        const channel = new MockOutputChannel(name);
        outputChannels.push(channel);

        return channel;
    },

    showInformationMessage: (message: string): Promise<undefined> => {
        informationMessages.push(message);

        return Promise.resolve(undefined);
    },

    showErrorMessage: (message: string): Promise<undefined> => {
        errorMessages.push(message);

        return Promise.resolve(undefined);
    },

    withProgress: async <T>(
        options: ProgressOptions,
        task: (
            progress: Progress<{ message?: string; increment?: number }>,
            token: CancellationToken,
        ) => Thenable<T>,
    ): Promise<T> => {
        const call: WithProgressCall = { options, reported: [] };
        withProgressCalls.push(call);

        return await task(
            {
                report: (value) => {
                    if (value.message !== undefined) {
                        call.reported.push(value.message);
                    }
                },
            },
            { isCancellationRequested: false },
        );
    },
};

export const commands = {
    registerCommand: (
        command: string,
        callback: (...args: unknown[]) => unknown,
    ): { dispose(): void } => {
        registeredCommands.set(command, callback);

        return {
            dispose: () => {
                registeredCommands.delete(command);
            },
        };
    },

    executeCommand: async (
        command: string,
        ...args: unknown[]
    ): Promise<unknown> => {
        const callback = registeredCommands.get(command);
        if (!callback) {
            throw new Error(`Command not registered: ${command}`);
        }

        return await callback(...args);
    },
};

/**
 * Clears everything the mock recorded. Call it from `beforeEach`.
 *
 * @returns Nothing.
 */
export const resetVscodeMock = (): void => {
    withProgressCalls.length = 0;
    informationMessages.length = 0;
    errorMessages.length = 0;
    outputChannels.length = 0;
    registeredCommands.clear();
};
