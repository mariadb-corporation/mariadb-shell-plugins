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

import { describe, expect, it } from "vitest";

import { BackendRequestHelper } from "../../../../app-logic/MigrationSubApp/BackendRequestHelper.js";
import { CheckState } from "../../../../components/ui/Checkbox/Checkbox.js";
import {
    CheckStatus,
    CompatibilityFlags,
    type IMigrationChecksData,
    MessageLevel,
    MigrationCheckStatus,
    MigrationStepStatus,
    SubStepId
} from "../../../../communication/ProtocolMigration.js";

describe("BackendRequestHelper", () => {
    const helper = new BackendRequestHelper();
    type BackendRequestState = Parameters<BackendRequestHelper["generateBackendRequest"]>[1];

    interface MigrationChecksRequest {
        issueResolution?: Record<string, CompatibilityFlags>;
        runChecks?: boolean;
        abortChecks?: boolean;
    }

    const createChecksData = (issues: IMigrationChecksData["issues"]): IMigrationChecksData => {
        return {
            issues,
            checkProgress: {
                completed: 0,
                total: 0,
                detail: "",
                currentCheckTitle: "",
                currentCheck: -1,
                completedChecks: 0,
                totalChecks: 0,
            },
            checkStatus: MigrationCheckStatus.DONE,
        };
    };

    const issues: IMigrationChecksData["issues"] = [{
        checkId: "user/no_password",
        level: MessageLevel.ERROR,
        title: "No password",
        result: "",
        description: "",
        objects: [],
        choices: [CompatibilityFlags.lock_invalid_accounts],
        status: CheckStatus.ACTION_REQUIRED,
    }];

    const state = {
        issueResolution: {
            "user/no_password": CompatibilityFlags.lock_invalid_accounts,
        },
        backendState: {
            [SubStepId.MIGRATION_CHECKS]: {
                status: MigrationStepStatus.IN_PROGRESS,
                errors: [],
                values: null,
                data: createChecksData(issues),
            },
        },
    } satisfies BackendRequestState;

    it("keeps runChecks in the default request used by the browser flow", () => {
        const request = JSON.parse(
            helper.generateBackendRequest(SubStepId.MIGRATION_CHECKS, state)
        ) as MigrationChecksRequest;

        expect(Object.keys(request)).toEqual(["runChecks", "issueResolution"]);
        expect(request).toEqual({
            runChecks: true,
            issueResolution: {
                "user/no_password": CompatibilityFlags.lock_invalid_accounts,
            },
        });
        expect("abortChecks" in request).toBe(false);
    });

    it("disables default runChecks once the checks step is already finished", () => {
        const request = JSON.parse(
            helper.generateBackendRequest(SubStepId.MIGRATION_CHECKS, {
                ...state,
                backendState: {
                    [SubStepId.MIGRATION_CHECKS]: {
                        ...state.backendState[SubStepId.MIGRATION_CHECKS],
                        status: MigrationStepStatus.FINISHED,
                        data: createChecksData([]),
                    },
                },
            })
        ) as MigrationChecksRequest;

        expect(Object.keys(request)).toEqual(["runChecks", "issueResolution"]);
        expect(request).toEqual({
            runChecks: false,
            issueResolution: {},
        });
        expect("abortChecks" in request).toBe(false);
    });

    it("keeps the selected issue resolutions when checks are rerun explicitly", () => {
        const runRequest = JSON.parse(helper.generateBackendRequest(
            SubStepId.MIGRATION_CHECKS,
            state,
            { runChecks: true }
        )) as MigrationChecksRequest;

        expect(Object.keys(runRequest)).toEqual(["runChecks", "issueResolution"]);
        expect(runRequest).toEqual({
            runChecks: true,
            issueResolution: {
                "user/no_password": CompatibilityFlags.lock_invalid_accounts,
            },
        });
        expect("abortChecks" in runRequest).toBe(false);
    });

    it("sends only abortChecks for abort requests", () => {
        const abortRequest = JSON.parse(helper.generateBackendRequest(
            SubStepId.MIGRATION_CHECKS,
            state,
            { abortChecks: true }
        )) as MigrationChecksRequest;

        expect(Object.keys(abortRequest)).toEqual(["abortChecks"]);
        expect(abortRequest).toEqual({
            abortChecks: true,
        });
        expect("runChecks" in abortRequest).toBe(false);
        expect("issueResolution" in abortRequest).toBe(false);
    });

    it("uses the request editor payload verbatim when enabled", () => {
        const request = JSON.parse(helper.generateBackendRequest(
            SubStepId.MIGRATION_CHECKS,
            {
                ...state,
                requestEditorEnabled: {
                    [SubStepId.MIGRATION_CHECKS]: CheckState.Checked,
                },
                subStepConfig: {
                    [SubStepId.MIGRATION_CHECKS]: JSON.stringify({
                        abortChecks: true,
                        issueResolution: {
                            "user/no_password": CompatibilityFlags.IGNORE,
                        },
                    }),
                },
            }
        )) as MigrationChecksRequest;

        expect(request).toEqual({
            abortChecks: true,
            issueResolution: {
                "user/no_password": CompatibilityFlags.IGNORE,
            },
        });
    });
});
