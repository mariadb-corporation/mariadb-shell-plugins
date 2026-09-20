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

export interface ShellVersion {
    major: number;
    minor: number;
    patch: number;
}

const VERSION_PATTERN = /(\d+)\.(\d+)(?:\.(\d+))?/;

/**
 * Parses a bare version string such as `26.9.2`, `v26.9.2` or `26.9`.
 * A missing patch level counts as 0.
 *
 * @param text The string to read the version from.
 *
 * @returns The parsed version, or undefined if the text holds no version.
 */
export const parseVersion = (text: string): ShellVersion | undefined => {
    const match = VERSION_PATTERN.exec(text.trim());
    if (!match) {
        return undefined;
    }

    return {
        major: Number(match[1]),
        minor: Number(match[2]),
        patch: match[3] === undefined ? 0 : Number(match[3]),
    };
};

/**
 * Parses the output of `mariadb-shell --version`, which looks like
 * `mariadb-shell   Ver 26.9.2 for osx10.21 on arm64 - for MariaDB 13.1.0 ...`.
 *
 * The `Ver` marker is honoured first, because the tail of the line carries
 * further version numbers - the platform and the server it was built for -
 * that must not be mistaken for the shell's own version.
 *
 * @param output The raw output of the `--version` call.
 *
 * @returns The shell version, or undefined if none could be read.
 */
export const parseShellVersionOutput = (
    output: string,
): ShellVersion | undefined => {
    const tagged = /\bVer\s+v?(\d+\.\d+(?:\.\d+)?)/i.exec(output);
    if (tagged) {
        return parseVersion(tagged[1]);
    }

    return parseVersion(output);
};

/**
 * Orders two versions.
 *
 * @param a The left hand version.
 * @param b The right hand version.
 *
 * @returns A negative number if a < b, 0 if they are equal, a positive
 *          number if a > b.
 */
export const compareVersions = (a: ShellVersion, b: ShellVersion): number => {
    return (
        a.major - b.major || a.minor - b.minor || a.patch - b.patch
    );
};

/**
 * Checks whether a version is at least the required minimum.
 *
 * @param version The version to check.
 * @param minimum The lowest acceptable version.
 *
 * @returns True if version >= minimum.
 */
export const meetsMinimum = (
    version: ShellVersion,
    minimum: ShellVersion,
): boolean => {
    return compareVersions(version, minimum) >= 0;
};

/**
 * Renders a version back into its `major.minor.patch` form.
 *
 * @param version The version to render.
 *
 * @returns The version as a string.
 */
export const formatVersion = (version: ShellVersion): string => {
    return `${version.major}.${version.minor}.${version.patch}`;
};
