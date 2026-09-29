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

import { MINIMUM_SHELL_VERSION } from "./constants.js";
import {
    installShell,
    type ProcessRunner,
    type ProgressHost,
} from "./installer.js";
import {
    describeLocation,
    installPrefix,
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
    /** Called when no usable shell was found and the installer starts. */
    onInstalling?: () => void;
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

    log(`Looking for MariaDB Shell ${minimumVersion} or newer.`);
    const existing = await locateShell(environment, minimumVersion, log);
    if (existing) {
        log(describeLocation(existing));

        return { location: existing, installed: false };
    }

    log(`No MariaDB Shell ${minimumVersion} or newer found. `
        + "Running the installer.");
    dependencies.onInstalling?.();

    await installShell(
        {
            platform: environment.platform,
            runner,
            progress,
            log,
        },
        minimumVersion,
    );

    const installed = await locateShell(environment, minimumVersion, log);
    if (!installed) {
        throw new Error(
            "The MariaDB Shell installer finished, but no MariaDB Shell "
            + `${minimumVersion} or newer could be found afterwards in `
            + `${installPrefix(environment)}.`,
        );
    }

    log(describeLocation(installed));

    return { location: installed, installed: true };
};
