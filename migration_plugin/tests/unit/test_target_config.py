# Copyright (c) 2026, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation. The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from migration_plugin.lib.backend import target_config


class FakeCompartment:

    def __init__(self, config, *args, **kwargs):
        self._config = config
        self.id = config["tenancy"]

    def list_availability_domains(self):
        return [SimpleNamespace(name=f'{self._config["region"]}-ad-1')]

    def list_db_versions(self):
        return [SimpleNamespace(version_family="8.4", versions=[])]

    def list_db_shapes(self, availability_domain=None):
        return [SimpleNamespace(name=f"{availability_domain}-shape")]


class TestConfigureTargetDBSystem:

    def test_region_specific_shape_cache_is_not_shared_between_instances(self):
        server_info = MagicMock()
        find_shared_ssh_key_cb = MagicMock()

        with patch(
            "migration_plugin.lib.backend.target_config.oci_utils.Compartment",
            FakeCompartment,
        ):
            ashburn = target_config.ConfigureTargetDBSystem(
                {
                    "tenancy": "ocid1.tenancy.oc1..exampleuniqueID",
                    "region": "us-ashburn-1",
                },
                find_shared_ssh_key_cb,
                server_info,
            )
            frankfurt = target_config.ConfigureTargetDBSystem(
                {
                    "tenancy": "ocid1.tenancy.oc1..exampleuniqueID",
                    "region": "eu-frankfurt-1",
                },
                find_shared_ssh_key_cb,
                server_info,
            )

            ashburn._load_compartment_capabilities()
            frankfurt._load_compartment_capabilities()

        assert list(ashburn.shapes) == ["us-ashburn-1-ad-1"]
        assert list(frankfurt.shapes) == ["eu-frankfurt-1-ad-1"]
