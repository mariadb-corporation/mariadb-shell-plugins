# Copyright (c) 2026, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software that is licensed
# under separate terms, as designated in a particular file or component
# or in included license documentation. The authors of MySQL hereby grant
# you an additional permission to link the program and your derivative
# works with the separately licensed software that they have either
# included with the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from unittest.mock import MagicMock, patch

from migration_plugin.lib import oci_utils


class TestCompartment:

    def test_list_region_subscriptions_uses_tenancy_id(self):
        config = {
            "tenancy": "ocid1.tenancy.oc1..exampleuniqueID",
            "region": "us-ashburn-1",
        }
        identity_client = MagicMock()
        pagination_result = MagicMock(data=["subscription"])

        with patch(
            "migration_plugin.lib.oci_utils.configuration.get_current_config",
            return_value=config,
        ), patch(
            "migration_plugin.lib.oci_utils.core.get_oci_identity_client",
            return_value=identity_client,
        ), patch(
            "migration_plugin.lib.oci_utils.core.get_oci_virtual_network_client"
        ), patch(
            "migration_plugin.lib.oci_utils.core.get_oci_compute_client"
        ), patch(
            "migration_plugin.lib.oci_utils.core.get_oci_db_system_client"
        ), patch(
            "migration_plugin.lib.oci_utils.core.get_oci_mds_client"
        ), patch(
            "migration_plugin.lib.oci_utils.core.get_oci_object_storage_client"
        ), patch(
            "migration_plugin.lib.oci_utils.oci.pagination.list_call_get_all_results",
            return_value=pagination_result,
        ) as list_all:
            compartment = oci_utils.Compartment(config, lazy_refresh=True)

            result = compartment.list_region_subscriptions()

        list_all.assert_called_once_with(
            identity_client.list_region_subscriptions,
            config["tenancy"],
        )
        assert result == pagination_result.data
