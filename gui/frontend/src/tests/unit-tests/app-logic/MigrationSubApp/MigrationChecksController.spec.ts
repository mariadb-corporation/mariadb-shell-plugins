/*
 * Copyright (c) 2026, Oracle and/or its affiliates.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 *
 * This program is designed to work with certain software (including
 * but not limited to OpenSSL) that is licensed under separate terms, as
 * designated in a particular file or component or in included license
 * documentation.  The authors of MySQL hereby grant you an additional
 * permission to link the program and your derivative works with the
 * separately licensed software that they have either included with
 * the program or referenced in the documentation.
 *
 * This program is distributed in the hope that it will be useful,  but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
 * the GNU General Public License, version 2.0, for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { MigrationChecksController, IMigrationChecksControllerState } from
    "../../../../app-logic/MigrationSubApp/MigrationChecksController.js";
import {
    MigrationChecksStatus,
    seedMigrationChecksIssueResolution
} from "../../../../app-logic/MigrationSubApp/MigrationChecksRuntime.js";
import { CompatibilityFlags, MigrationStepStatus, SubStepId } from "../../../../communication/ProtocolMigration.js";

afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
});

describe("seedMigrationChecksIssueResolution", () => {
    it("adds defaults only for newly seen issues", () => {
        const nextIssueResolution = seedMigrationChecksIssueResolution({
            existing: CompatibilityFlags.IGNORE,
        }, {
            issues: [{
                checkId: "existing",
                level: "WARNING",
                title: "Existing issue",
                result: "",
                description: "",
                objects: [],
                choices: [CompatibilityFlags.create_invisible_pks],
                status: 0,
            }, {
                checkId: "new",
                level: "WARNING",
                title: "New issue",
                result: "",
                description: "",
                objects: [],
                choices: [CompatibilityFlags.create_invisible_pks, CompatibilityFlags.IGNORE],
                status: 0,
            }, {
                checkId: "without-choice",
                level: "WARNING",
                title: "No choice issue",
                result: "",
                description: "",
                objects: [],
                choices: [],
                status: 0,
            }],
        });

        expect(nextIssueResolution).toEqual({
            existing: CompatibilityFlags.IGNORE,
            new: CompatibilityFlags.create_invisible_pks,
        });
    });

    it("returns undefined when nothing new needs to be seeded", () => {
        const nextIssueResolution = seedMigrationChecksIssueResolution({
            existing: CompatibilityFlags.IGNORE,
        }, {
            issues: [{
                checkId: "existing",
                level: "WARNING",
                title: "Existing issue",
                result: "",
                description: "",
                objects: [],
                choices: [CompatibilityFlags.create_invisible_pks],
                status: 0,
            }],
        });

        expect(nextIssueResolution).toBeUndefined();
    });
});

describe("MigrationChecksController", () => {
    const createPendingState = () => {
        return {
            status: MigrationStepStatus.IN_PROGRESS,
            errors: [],
            values: null,
            data: {
                issues: [],
                checkStatus: MigrationChecksStatus.PENDING,
                checkProgress: {
                    completed: 0,
                    total: 0,
                    detail: "",
                    currentCheckTitle: "",
                    currentCheck: -1,
                    completedChecks: 0,
                    totalChecks: 0,
                },
            },
        };
    };

    it("auto-runs checks after entering the migration checks step", () => {
        const state: IMigrationChecksControllerState = {
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            backendRequestInProgress: false,
            stepState: createPendingState(),
        };
        const updateStep = vi.fn(() => {
            return Promise.resolve();
        });
        const commitStep = vi.fn(() => {
            return Promise.resolve();
        });
        const controller = new MigrationChecksController({
            getState: () => {
                return state;
            },
            updateStep,
            commitStep,
        });

        controller.sync({
            currentSubStepId: SubStepId.SCHEMA_SELECTION,
            backendRequestInProgress: false,
            stepState: undefined,
        });

        expect(updateStep).toHaveBeenCalledTimes(1);
        expect(updateStep).toHaveBeenCalledWith({ runChecks: true });
        expect(commitStep).not.toHaveBeenCalled();
    });

    it("polls running checks without using the command update path", async () => {
        vi.useFakeTimers();

        const state: IMigrationChecksControllerState = {
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            backendRequestInProgress: false,
            stepState: {
                ...createPendingState(),
                data: {
                    ...createPendingState().data,
                    checkStatus: MigrationChecksStatus.RUNNING_COMPATIBILITY_CHECKS,
                },
            },
        };
        const updateStep = vi.fn(() => {
            return Promise.resolve();
        });
        const pollStep = vi.fn(() => {
            return Promise.resolve();
        });
        const commitStep = vi.fn(() => {
            return Promise.resolve();
        });
        const controller = new MigrationChecksController({
            getState: () => {
                return state;
            },
            updateStep,
            pollStep,
            commitStep,
        });

        controller.sync({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            backendRequestInProgress: false,
            stepState: createPendingState(),
        });

        await vi.advanceTimersByTimeAsync(1000);

        expect(pollStep).toHaveBeenCalledTimes(1);
        expect(updateStep).not.toHaveBeenCalled();
        expect(commitStep).not.toHaveBeenCalled();
    });

    it("auto-commits after the checks step becomes ready", () => {
        const state: IMigrationChecksControllerState = {
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            backendRequestInProgress: false,
            stepState: {
                ...createPendingState(),
                status: MigrationStepStatus.READY_TO_COMMIT,
                data: {
                    ...createPendingState().data,
                    checkStatus: MigrationChecksStatus.DONE,
                },
            },
        };
        const updateStep = vi.fn(() => {
            return Promise.resolve();
        });
        const commitStep = vi.fn(() => {
            return Promise.resolve();
        });
        const controller = new MigrationChecksController({
            getState: () => {
                return state;
            },
            updateStep,
            commitStep,
        });

        controller.sync({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            backendRequestInProgress: false,
            stepState: createPendingState(),
        });

        expect(commitStep).toHaveBeenCalledTimes(1);
        expect(updateStep).not.toHaveBeenCalled();
    });

});
