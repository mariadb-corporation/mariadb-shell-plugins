# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""The standardized names the dba plugin speaks, whatever the vendor.

Every vendor calls its objects something else - a Hetzner "server" is an
UpCloud "server" too, but a MariaDB Cloud "service" is neither - so the
plugin names them once, here, and each vendor plugin maps these names onto
its own API. A caller only ever sees these names.
"""

# cSpell:ignore mysqlsh MariaDB

from dba_plugin.lib.general import DbaError

# --- Feature sets ----------------------------------------------------------

# Managed MariaDB services the vendor runs: the vendor deploys and operates
# the server, the user gets an endpoint.
MANAGED_SERVICE = "ManagedService"
# Infrastructure to run MariaDB Server or a Galera Cluster on: VMs, their
# disks, networks and what goes with them.
CORE_COMPUTE = "CoreCompute"

FEATURE_SETS = (MANAGED_SERVICE, CORE_COMPUTE)

# --- Object types ----------------------------------------------------------

MANAGED_MARIADB_SERVICES = "managed_mariadb_services"
VMS = "vms"
VOLUMES = "volumes"
NETWORKING = "networking"
FIREWALLS = "firewalls"
LOAD_BALANCERS = "load_balancers"
DNS = "dns"
OBJECT_STORAGE = "object_storage"
SSH_KEYS = "ssh_keys"

# Each type with what it is and the feature set it belongs to, in the order
# they are reported.
OBJECT_TYPES = {
    MANAGED_MARIADB_SERVICES: {
        "description": "MariaDB servers and clusters the vendor deploys and operates.",
        "feature_set": MANAGED_SERVICE,
    },
    VMS: {
        "description": "Virtual machines to run MariaDB Server or Galera Cluster nodes on.",
        "feature_set": CORE_COMPUTE,
    },
    VOLUMES: {
        "description": "Block storage volumes that can be attached to a VM.",
        "feature_set": CORE_COMPUTE,
    },
    NETWORKING: {
        "description": "Private networks connecting VMs.",
        "feature_set": CORE_COMPUTE,
    },
    FIREWALLS: {
        "description": "Firewall rule sets applied to VMs.",
        "feature_set": CORE_COMPUTE,
    },
    LOAD_BALANCERS: {
        "description": "Load balancers distributing connections across VMs.",
        "feature_set": CORE_COMPUTE,
    },
    DNS: {
        "description": "DNS zones and their records.",
        "feature_set": CORE_COMPUTE,
    },
    OBJECT_STORAGE: {
        "description": "S3-compatible object storage, for backups.",
        "feature_set": CORE_COMPUTE,
    },
    SSH_KEYS: {
        "description": "Public SSH keys installed on VMs when they are created.",
        "feature_set": CORE_COMPUTE,
    },
}


def check_feature_set(feature_set) -> str:
    """Returns the feature set named, refusing one that does not exist.

    Args:
        feature_set (str): A feature set name; case does not matter.

    Returns:
        str: The feature set, spelled as :data:`FEATURE_SETS` spells it.

    Raises:
        DbaError: The name is no feature set.
    """
    for known in FEATURE_SETS:
        if str(feature_set).lower() == known.lower():
            return known

    raise DbaError(
        f"Unknown feature set '{feature_set}'. Use one of: "
        + ", ".join(FEATURE_SETS)
        + "."
    )


def check_object_type(object_type) -> str:
    """Returns the object type named, refusing one that does not exist.

    Args:
        object_type (str): An object type name; case does not matter.

    Returns:
        str: The object type, as :data:`OBJECT_TYPES` spells it.

    Raises:
        DbaError: The name is no object type.
    """
    name = str(object_type or "").strip().lower()
    if name in OBJECT_TYPES:
        return name

    raise DbaError(
        f"Unknown object type '{object_type}'. Use one of: "
        + ", ".join(OBJECT_TYPES)
        + "."
    )
