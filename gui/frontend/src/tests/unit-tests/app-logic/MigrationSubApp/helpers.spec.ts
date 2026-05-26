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

import { describe, expect, it, vi } from "vitest";

import {
    buildOciNetworkingRefreshState,
    buildOciRegionOptions,
    ociResourcesToOptions,
} from "../../../../app-logic/MigrationSubApp/helpers.js";

describe("MigrationSubApp helpers", () => {
    it("builds unique OCI region options with the current region first", () => {
        expect(buildOciRegionOptions("eu-frankfurt-1", [
            "us-ashburn-1",
            "eu-frankfurt-1",
            "",
            "uk-london-1",
        ])).toEqual([
            { id: "eu-frankfurt-1", label: "eu-frankfurt-1" },
            { id: "us-ashburn-1", label: "us-ashburn-1" },
            { id: "uk-london-1", label: "uk-london-1" },
        ]);
    });

    it("maps OCI resources to dropdown options", () => {
        expect(ociResourcesToOptions([
            { id: "vcn-1", displayName: "Application VCN" },
            { id: "subnet-1", displayName: "Private Subnet" },
        ])).toEqual([
            { id: "vcn-1", label: "Application VCN" },
            { id: "subnet-1", label: "Private Subnet" },
        ]);
    });

    it("clears networking selections when no network compartment is selected", async () => {
        const fetchVcns = vi.fn(() => {
            return Promise.resolve([{ id: "vcn-1", displayName: "Application VCN" }]);
        });
        const fetchSubnets = vi.fn(() => {
            return Promise.resolve([{ id: "subnet-1", displayName: "Private Subnet" }]);
        });

        await expect(buildOciNetworkingRefreshState({
            profile: "DEFAULT",
            selectedVcn: "vcn-1",
            privateSubnet: "private-subnet",
            publicSubnet: "public-subnet",
            fetchVcns,
            fetchSubnets,
        })).resolves.toEqual({
            vcns: [],
            subnets: [],
            formGroupValues: {
                "hosting.vcnId": "",
                "hosting.privateSubnet.id": "",
                "hosting.publicSubnet.id": "",
            },
        });
        expect(fetchVcns).not.toHaveBeenCalled();
        expect(fetchSubnets).not.toHaveBeenCalled();
    });

    it("clears stale VCN and subnet selections after region refresh", async () => {
        const fetchVcns = vi.fn(() => {
            return Promise.resolve([{ id: "vcn-2", displayName: "Other VCN" }]);
        });
        const fetchSubnets = vi.fn(() => {
            return Promise.resolve([{ id: "subnet-1", displayName: "Private Subnet" }]);
        });

        await expect(buildOciNetworkingRefreshState({
            profile: "DEFAULT",
            networkCompartment: "network-compartment",
            selectedVcn: "vcn-1",
            privateSubnet: "private-subnet",
            publicSubnet: "public-subnet",
            fetchVcns,
            fetchSubnets,
        })).resolves.toEqual({
            vcns: [{ id: "vcn-2", label: "Other VCN" }],
            subnets: [],
            formGroupValues: {
                "hosting.vcnId": "",
                "hosting.privateSubnet.id": "",
                "hosting.publicSubnet.id": "",
            },
        });
        expect(fetchVcns).toHaveBeenCalledWith("DEFAULT", "network-compartment");
        expect(fetchSubnets).not.toHaveBeenCalled();
    });

    it("clears stale subnet selections when no VCN is selected", async () => {
        const fetchVcns = vi.fn(() => {
            return Promise.resolve([{ id: "vcn-1", displayName: "Application VCN" }]);
        });
        const fetchSubnets = vi.fn(() => {
            return Promise.resolve([{ id: "subnet-1", displayName: "Private Subnet" }]);
        });

        await expect(buildOciNetworkingRefreshState({
            profile: "DEFAULT",
            networkCompartment: "network-compartment",
            privateSubnet: "private-subnet",
            publicSubnet: "public-subnet",
            fetchVcns,
            fetchSubnets,
        })).resolves.toEqual({
            vcns: [{ id: "vcn-1", label: "Application VCN" }],
            subnets: [],
            formGroupValues: {
                "hosting.privateSubnet.id": "",
                "hosting.publicSubnet.id": "",
            },
        });
        expect(fetchVcns).toHaveBeenCalledWith("DEFAULT", "network-compartment");
        expect(fetchSubnets).not.toHaveBeenCalled();
    });

    it("keeps valid networking selections and clears stale subnets", async () => {
        const fetchVcns = vi.fn(() => {
            return Promise.resolve([{ id: "vcn-1", displayName: "Application VCN" }]);
        });
        const fetchSubnets = vi.fn(() => {
            return Promise.resolve([{ id: "private-subnet", displayName: "Private Subnet" }]);
        });

        await expect(buildOciNetworkingRefreshState({
            profile: "DEFAULT",
            networkCompartment: "network-compartment",
            selectedVcn: "vcn-1",
            privateSubnet: "private-subnet",
            publicSubnet: "public-subnet",
            fetchVcns,
            fetchSubnets,
        })).resolves.toEqual({
            vcns: [{ id: "vcn-1", label: "Application VCN" }],
            subnets: [{ id: "private-subnet", label: "Private Subnet" }],
            formGroupValues: {
                "hosting.publicSubnet.id": "",
            },
        });
        expect(fetchSubnets).toHaveBeenCalledWith("DEFAULT", "vcn-1");
    });
});
