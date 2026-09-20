/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
    ProgressLocation,
    resetVscodeMock,
    withProgressCalls,
} from "../test/mocks/vscode.js";
import { createNotificationProgressHost } from "./vscodeProgress.js";

describe("createNotificationProgressHost", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("shows the progress as a notification", async () => {
        const host = createNotificationProgressHost();

        await host.withProgress("Installing MariaDB Shell 26.9.2",
            async () => {
                // Nothing to do - only the notification is under test.
            });

        expect(withProgressCalls).toHaveLength(1);
        expect(withProgressCalls[0].options).toEqual({
            location: ProgressLocation.Notification,
            title: "Installing MariaDB Shell 26.9.2",
            cancellable: false,
        });
    });

    it("forwards the reported messages", async () => {
        const host = createNotificationProgressHost();

        await host.withProgress("Installing", async (report) => {
            report("Downloading");
            report("Unpacking into /opt/mariadb-shell");
        });

        expect(withProgressCalls[0].reported).toEqual([
            "Downloading",
            "Unpacking into /opt/mariadb-shell",
        ]);
    });

    it("passes the task's result through", async () => {
        const host = createNotificationProgressHost();

        await expect(host.withProgress("Installing", async () => {
            return 0;
        })).resolves.toBe(0);
    });

    it("propagates a failing task", async () => {
        const host = createNotificationProgressHost();

        await expect(host.withProgress("Installing", async () => {
            throw new Error("curl failed");
        })).rejects.toThrow("curl failed");
    });
});
