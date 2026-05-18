# Copyright (c) 2026, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from unittest.mock import MagicMock, patch

from migration_plugin.lib.backend import model, target_config


def _fake_root_compartment():
    root = MagicMock()
    root.id = "ocid1.tenancy.oc1..root"
    root.display_name = "root"
    return root


def _target_configurator():
    with patch(
        "migration_plugin.lib.backend.target_config.oci_utils.Compartment",
        return_value=_fake_root_compartment(),
    ):
        return target_config.ConfigureTargetDBSystem(
            oci_config={},
            find_shared_ssh_key_cb=lambda _instance_id=None: None,
            server_info=model.ServerInfo(),
            override_root_compartment_id="ocid1.tenancy.oc1..root",
        )


def _valid_hosting_options(on_premise_public_cidr_block: str):
    options = model.OCIHostingOptions()
    options.parentCompartmentId = "ocid1.tenancy.oc1..root"
    options.compartmentName = "MySQL"
    options.createVcn = True
    options.networkParentCompartmentId = "ocid1.tenancy.oc1..root"
    options.networkCompartmentName = "Networks"
    options.vcnName = "MySQLVCN"
    options.vcnCidrBlock = "10.0.0.0/16"
    options.internetGatewayName = "MySQLVCN-IGW"
    options.serviceGatewayName = "MySQLVCN-SGW"
    options.privateSubnet.name = "MySQLSubnet"
    options.privateSubnet.cidrBlock = "10.0.2.0/24"
    options.publicSubnet.name = "MySQLPublicSubnet"
    options.publicSubnet.cidrBlock = "10.0.1.0/24"
    options.onPremisePublicCidrBlock = on_premise_public_cidr_block
    options.computeName = "mysql-jump-host"
    options.shapeName = "VM.Standard.E5.Flex"
    options.bucketName = "mysql-migrated-data"
    return options


def _target_options_issues_for(options: model.OCIHostingOptions):
    configurator = _target_configurator()
    with patch.object(configurator, "_select_target_compartment", return_value=[]):
        return configurator.validate_target_options(options, changed_options=None)


def _issue_inputs(issues):
    return [issue.info["input"] for issue in issues]


class TestTargetOptionsPublicCidr:

    def test_recommended_options_use_detected_ipv4_cidr(self):
        configurator = _target_configurator()

        with patch.object(target_config.util, "get_my_public_ip") as get_my_public_ip:
            get_my_public_ip.return_value = "192.0.2.44"
            with patch.object(configurator, "resolve_existing_resources"), patch.object(
                configurator, "resolve_existing_vcn"
            ), patch.object(configurator, "resolve_jump_host"):
                options = configurator.get_recommended_oci_options()

        assert options.onPremisePublicCidrBlock == "192.0.2.44/32"

    def test_recommended_options_leave_empty_cidr_when_ipv4_is_not_detected(self):
        configurator = _target_configurator()

        with patch.object(target_config.util, "get_my_public_ip") as get_my_public_ip:
            get_my_public_ip.return_value = ""
            with patch.object(configurator, "resolve_existing_resources"), patch.object(
                configurator, "resolve_existing_vcn"
            ), patch.object(configurator, "resolve_jump_host"):
                options = configurator.get_recommended_oci_options()

        assert options.onPremisePublicCidrBlock == ""

    def test_validate_target_options_accepts_ipv4_cidr(self):
        issues = _target_options_issues_for(_valid_hosting_options("192.0.2.44/32"))

        assert "hosting.onPremisePublicCidrBlock" not in _issue_inputs(issues)

    def test_validate_target_options_accepts_bare_ipv4_as_host_cidr(self):
        issues = _target_options_issues_for(_valid_hosting_options("192.0.2.44"))

        assert "hosting.onPremisePublicCidrBlock" not in _issue_inputs(issues)

    def test_validate_target_options_rejects_empty_public_cidr(self):
        issues = _target_options_issues_for(_valid_hosting_options(""))

        assert "hosting.onPremisePublicCidrBlock" in _issue_inputs(issues)

    def test_validate_target_options_rejects_ipv6_public_cidr(self):
        issues = _target_options_issues_for(_valid_hosting_options("2001:db8::1/128"))

        assert "hosting.onPremisePublicCidrBlock" in _issue_inputs(issues)

    def test_validate_target_options_rejects_host_bits_in_public_cidr(self):
        issues = _target_options_issues_for(_valid_hosting_options("192.0.2.44/24"))

        assert "hosting.onPremisePublicCidrBlock" in _issue_inputs(issues)
