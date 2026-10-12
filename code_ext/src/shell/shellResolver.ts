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

import type { BootstrapResult } from "./bootstrap.js";

/**
 * Locates a shell that is new enough, or installs one.
 *
 * @param onInstalling Called when the installer starts.
 */
export type ShellResolveSteps = (
    onInstalling: () => void,
) => Promise<BootstrapResult>;

/** What a consumer of the shell needs: getting it there. */
export interface IShellResolver {
    /**
     * @param onInstalling Called if, and when, the installer runs.
     *
     * @returns The shell, once it is there.
     */
    ensure(onInstalling?: () => void): Promise<BootstrapResult>;
}

/** One lookup under way: its outcome, and when its installer started. */
interface ILookup {
    result: Promise<BootstrapResult>;
    installing: Promise<void>;
}

/**
 * Finds or installs the shell, once, however many callers ask at the same
 * time.
 *
 * Two servers run on the shell: the extension's own, which the
 * `ServerStarter` brings up, and the one Copilot starts through the MCP
 * server definition provider. Both can ask for a shell the moment the
 * window opens, and without this each would run the installer into the
 * same directory.
 */
export class ShellResolver implements IShellResolver {
    #pending?: ILookup;

    /**
     * @param steps How a shell is found or installed.
     */
    public constructor(private readonly steps: ShellResolveSteps) { }

    /**
     * Looks the shell up, or joins the lookup already under way.
     *
     * @param onInstalling Called if, and when, the installer runs - also for
     *        a caller that joins after it has started.
     *
     * @returns The shell, once it is there.
     */
    public async ensure(onInstalling?: () => void): Promise<BootstrapResult> {
        const lookup = this.#pending ??= this.#start();
        if (onInstalling) {
            void lookup.installing.then(onInstalling);
        }

        return await lookup.result;
    }

    /**
     * Starts a lookup, which is forgotten once it is over.
     *
     * @returns The lookup.
     */
    #start(): ILookup {
        let installStarted: () => void = () => { /* set below */ };
        const installing = new Promise<void>((resolve) => {
            installStarted = resolve;
        });
        const result = this.steps(() => { installStarted(); })
            .finally(() => {
                this.#pending = undefined;
            });

        return { result, installing };
    }
}
