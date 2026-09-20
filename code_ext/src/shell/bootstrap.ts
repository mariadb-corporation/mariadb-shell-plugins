/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { MINIMUM_SHELL_VERSION } from "./constants.js";
import {
    installShell,
    type ProcessRunner,
    type ProgressHost,
} from "./installer.js";
import {
    describeLocation,
    locateShell,
    type ShellEnvironment,
    type ShellLocation,
} from "./locator.js";

export interface BootstrapDependencies {
    environment: ShellEnvironment;
    runner: ProcessRunner;
    progress: ProgressHost;
    log: (message: string) => void;
    /** Overridable so tests need not pin themselves to the shipped value. */
    minimumVersion?: string;
}

export interface BootstrapResult {
    location: ShellLocation;
    /** True if the shell had to be downloaded first. */
    installed: boolean;
}

/**
 * Makes sure a MariaDB Shell of at least the required version is available,
 * installing it if neither the PATH nor the local installation directory
 * holds one.
 *
 * @param dependencies The environment, process runner, progress UI and log
 *                     to work with.
 *
 * @returns The shell that was found, and whether it had to be installed.
 */
export const ensureShell = async (
    dependencies: BootstrapDependencies,
): Promise<BootstrapResult> => {
    const { environment, runner, progress, log } = dependencies;
    const minimumVersion = dependencies.minimumVersion
        ?? MINIMUM_SHELL_VERSION;

    const existing = await locateShell(environment, minimumVersion);
    if (existing) {
        log(describeLocation(existing));

        return { location: existing, installed: false };
    }

    log(`No MariaDB Shell ${minimumVersion} or newer found. `
        + "Running the installer.");

    await installShell(
        {
            platform: environment.platform,
            runner,
            progress,
        },
        minimumVersion,
    );

    const installed = await locateShell(environment, minimumVersion);
    if (!installed) {
        throw new Error(
            "The MariaDB Shell installer finished, but no MariaDB Shell "
            + `${minimumVersion} or newer could be found afterwards.`,
        );
    }

    log(describeLocation(installed));

    return { location: installed, installed: true };
};
