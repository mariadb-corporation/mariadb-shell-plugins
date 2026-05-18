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
 * separately licensed software that they have included with
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
/* eslint-disable no-restricted-syntax */

import { test, expect } from "@playwright/test";
import { Misc, browser } from "../lib/Misc.js";
import { PasswordDialog } from "../lib/PasswordDialog.js";
import { MigrationAssistantPage } from "../lib/MigrationAssistantPage.js";
import * as constants from "../lib/constants.js";

const migrationAssistant = new MigrationAssistantPage();

test.describe("Target options validation", () => {

    // eslint-disable-next-line no-empty-pattern
    test.beforeAll(async ({ }, testInfo) => {
        await Misc.loadPage(testInfo.titlePath[1]);

        const passwordDialog = new PasswordDialog();
        if (await passwordDialog.exists()) {
            await passwordDialog.setCredentials(String(process.env.DBROOTPASSWORD));
        }

        await Misc.waitForLoadingIcon();
    });

    test.afterAll(async () => {
        await browser.close();
    });

    test("Jump host source CIDR must be a valid IPv4 CIDR", async () => {
        await fillTargetOptions();

        await migrationAssistant.migrationPlan.setOnPremisePublicCidrBlock("");
        await migrationAssistant.next();
        await assertTargetSelectionRemainsOpen();
        await expect(migrationAssistant.migrationPlan.onPremisePublicCidrBlockErrors())
            .toContainText("Required value missing");

        await migrationAssistant.migrationPlan.setOnPremisePublicCidrBlock("2001:db8::1/128");
        await migrationAssistant.next();
        await assertTargetSelectionRemainsOpen();
        await expect(migrationAssistant.migrationPlan.onPremisePublicCidrBlockErrors())
            .toContainText("Invalid IPv4 CIDR block");

        await migrationAssistant.migrationPlan.setOnPremisePublicCidrBlock("0.0.0.0/0");
        await migrationAssistant.next();

        await migrationAssistant.migrationPlan.waitUntilStepIs({
            caption: constants.targetSelection,
            isExpanded: false
        }, constants.wait1second * 15);

        await migrationAssistant.migrationPlan.waitUntilStepIs({
            caption: constants.migrationType,
            isExpanded: true
        }, constants.wait1second * 15);
    });

});

const fillTargetOptions = async (): Promise<void> => {
    await Misc.dismissNotifications();
    await migrationAssistant.migrationPlan.waitUntilStepIs({
        caption: constants.targetSelection,
        isExpanded: true
    }, constants.wait1second * 5);

    await migrationAssistant.migrationPlan.setOciConfigProfile(String(process.env.MYSQLSH_OCI_CONFIG_PROFILE));
    await migrationAssistant.migrationPlan.setOciCompartment("——MySQLShellTesting");
    await migrationAssistant.migrationPlan.setOciNetwork("create_new");
    await migrationAssistant.migrationPlan
        .setConfigTemplate("Small Development Setup - 2 ECPU, 16GB RAM, 1Gbps NET");
    await migrationAssistant.migrationPlan.setDisplayName("E2E CIDR validation test");
    await migrationAssistant.migrationPlan.setAdminUsername("root");
    await migrationAssistant.migrationPlan.setPassword(process.env.DBROOTPASSWORD!);
    await migrationAssistant.migrationPlan.setConfirmPassword(process.env.DBROOTPASSWORD!);
};

const assertTargetSelectionRemainsOpen = async (): Promise<void> => {
    await migrationAssistant.migrationPlan.waitUntilStepIs({
        caption: constants.targetSelection,
        isExpanded: true
    }, constants.wait1second * 5);
};
