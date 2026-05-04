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

import {
    getMigrationSetupStatusMessage,
    parseOciSignInInfo
} from "../../../../app-logic/MigrationSubApp/MigrationSubApp.js";

describe("MigrationSubApp status helpers", () => {
    it("extracts OCI sign-in progress messages from JSON data", () => {
        const signInInfo = parseOciSignInInfo(JSON.stringify({
            message: "Open the OCI sign-in page.",
            info: {
                url: "https://example.com/sign-in",
            },
        }));

        expect(signInInfo.message).toBe("Open the OCI sign-in page.");
        expect(signInInfo.info?.url).toBe("https://example.com/sign-in");
    });

    it("keeps OCI sign-in progress readable when data is not JSON", () => {
        const signInInfo = parseOciSignInInfo("Authentication completed successfully.");

        expect(signInInfo.message).toBe("Authentication completed successfully.");
    });

    it("uses the parsed OCI sign-in message for the footer status", () => {
        const signInInfo = parseOciSignInInfo(JSON.stringify({
            message: "Waiting for OCI authentication...",
        }));

        expect(getMigrationSetupStatusMessage({
            ociLoginInProgress: true,
            ociSignInStatusMessage: signInInfo.message,
        })).toBe("Waiting for OCI authentication...");
    });

    it("keeps OCI sign-in status through the whole sign-in operation", () => {
        expect(getMigrationSetupStatusMessage({
            ociLoginInProgress: true,
            ociSignInStatusMessage: "Loading OCI profile...",
        })).toBe("Loading OCI profile...");
    });

    it("appends the new OCI API key hint to later spinner status messages in the same session", () => {
        const hint = "This may take longer than usual because the new OCI API keys need time to propagate.";

        expect(getMigrationSetupStatusMessage({
            isFetchingVcns: true,
            ociNewApiKeyUploadedThisSession: true,
        })).toBe(`Loading virtual cloud networks... ${hint}`);

        expect(getMigrationSetupStatusMessage({
            backendRequestInProgress: true,
            backendRequestStatusMessage: "Refreshing migration plan...",
            ociNewApiKeyUploadedThisSession: true,
        })).toBe(`Refreshing migration plan... ${hint}`);
    });

    it("uses backend request status messages while the backend spinner is active", () => {
        expect(getMigrationSetupStatusMessage({
            backendRequestInProgress: true,
            backendRequestStatusMessage: "Refreshing migration plan...",
        })).toBe("Refreshing migration plan...");
    });

    it("uses target selection loading messages while target spinners are active", () => {
        expect(getMigrationSetupStatusMessage({
            isFetchingShapes: true,
        })).toBe("Loading available shapes...");
    });
});
