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

"""The interface every vendor plugin implements.

A vendor plugin subclasses :class:`Vendor`, sets its class attributes and
implements, per object type it supports, the methods named after it:

* ``_list_<type>()`` - REQUIRED; what defines that the type is supported.
  Returns every deployed object of the type, each made with
  :func:`make_object`. The dba plugin filters by tags and project itself.
* ``_info_<type>()`` - what can be deployed: the sizes, locations, images,
  versions and prices the vendor offers. Returns a dict of lists.
* ``_deploy_<type>(settings)`` - deploys one object and returns it, made
  with :func:`make_object`.
* ``_delete_<type>(obj)`` - deletes the object :meth:`Vendor.find_object`
  found, and returns a dict describing what the vendor did (an action id,
  a status), or None. Deleting a VM deletes its system disk only: volumes
  attached to it - a database's data - are kept.
* ``_status_<type>(obj)`` - the object's current status. Optional: by
  default the object as listed is its status.

An operation a vendor does not implement for a type is refused with a
message naming what it does implement, so a vendor can support listing a
type without supporting deploying it.

The ``settings`` of a deployment use the standardized keys documented with
:data:`SETTINGS`, plus ``vendor_settings``: a dict passed through into the
vendor's own request body, for what the standardized keys do not cover.
"""

# cSpell:ignore mysqlsh MariaDB

from typing import Optional

from dba_plugin.lib import object_types as types
from dba_plugin.lib.general import DbaError
from dba_plugin.lib.rest_client import VendorApiError

# The label every object deployed for a named project carries, for vendors
# whose API has no projects of its own.
PROJECT_LABEL = "dba_project"

# The status of an object the vendor gives no state, such as a network or an
# SSH key: it exists, and that is all there is to it.
STATUS_ACTIVE = "active"

# The statuses, in any vendor's words, of an object that is ready to use: what
# waiting for a deployment waits for. A status naming a failure or an error
# ends the wait as failed.
READY_STATUSES = frozenset(("running", "started", "available", "online", "active", "ready", "ok"))
FAILED_STATUS_WORDS = ("fail", "error")
# The statuses of a server deployed not to start - Hetzner's start_after_create
# false, an UpCloud server created stopped: its deployment is done, too.
STOPPED_STATUSES = frozenset(("off", "stopped"))

# The standardized settings keys per object type, with what each means. A
# vendor maps the ones it supports onto its API, refuses those it does not
# and passes ``vendor_settings`` through.
COMMON_SETTINGS = {
    "name": "The object's name. Required.",
    "labels": "A dict of key/value labels (tags) to put on the object.",
    "vendor_settings": "A dict merged into the vendor's own request body as is.",
}
SETTINGS = {
    types.MANAGED_MARIADB_SERVICES: {
        "topology": "The service topology, e.g. 'es-single', 'es-replica', 'galera'.",
        "version": "The MariaDB server version.",
        "size": "The instance size (see object_info 'sizes').",
        "location": "The region (see object_info 'locations').",
        "provider": "The cloud provider the service runs on, where the vendor offers several.",
        "nodes": "The number of nodes.",
        "storage_gb": "The storage size in GB.",
        "allowed_ips": "A list of IP addresses or CIDR ranges allowed to connect.",
    },
    types.VMS: {
        "size": "The VM size, the vendor's server type or plan (see object_info 'sizes').",
        "location": "The location or zone (see object_info 'locations').",
        "image": "The OS image (see object_info 'images').",
        "ssh_keys": "A list of SSH keys to install: key names or ids where the vendor stores keys (see ssh_keys), else the public keys themselves.",
        "user_data": "Cloud-init user data.",
        "networks": "A list of private network names or ids to attach.",
        "firewalls": "A list of firewall names or ids to apply.",
    },
    types.VOLUMES: {
        "size_gb": "The size in GB.",
        "location": "The location or zone; not needed with vm.",
        "vm": "The name or id of a VM to attach the volume to.",
        "format": "The filesystem to format the volume with, where the vendor offers that.",
    },
    types.NETWORKING: {
        "ip_range": "The network's IP range in CIDR notation, e.g. 10.0.0.0/16.",
        "location": "The location or network zone.",
    },
    types.FIREWALLS: {
        "rules": "A list of rules: {direction, protocol, port, source_ips | destination_ips, description}.",
        "vms": "A list of VM names or ids to apply the firewall to.",
    },
    types.LOAD_BALANCERS: {
        "size": "The load balancer type (see object_info 'sizes').",
        "location": "The location or zone.",
        "network": "The name or id of a private network to attach it to.",
        "targets": "A list of VM names or ids to balance across.",
        "services": "A list of {protocol, listen_port, destination_port}.",
    },
    types.DNS: {
        "ttl": "The zone's default TTL in seconds.",
        "records": "A list of {name, type, value, ttl} records to create.",
    },
    types.OBJECT_STORAGE: {
        "location": "The region.",
        "size": "The storage size or tier, where the vendor sells it in sizes.",
    },
    types.SSH_KEYS: {
        "public_key": "The public key, in OpenSSH format. Required.",
    },
}


def settings_keys(object_type: str) -> dict:
    """The standardized settings of an object type, with their meanings."""
    return {**COMMON_SETTINGS, **SETTINGS.get(object_type, {})}


def make_object(
    object_type: str,
    id,
    name: str,
    status: Optional[str] = None,
    location: Optional[str] = None,
    labels: Optional[dict] = None,
    created: Optional[str] = None,
    vendor_type: Optional[str] = None,
    **properties,
) -> dict:
    """An object as every dba operation reports it, whatever the vendor.

    Args:
        object_type (str): The standardized object type.
        id: The vendor's id of the object; reported as a string.
        name (str): Its name.
        status (str): Its status, in the vendor's own words, or
            STATUS_ACTIVE for an object the vendor gives no state.
        location (str): Where it runs: a location, zone or region.
        labels (dict): Its labels or tags, as a dict; a tag without a value
            maps to "".
        created (str): When it was created, in ISO 8601.
        vendor_type (str): What the vendor calls this kind of object.
        **properties: What else matters about it - addresses, size, the
            server it is attached to - under keys of the vendor's choosing.

    Returns:
        dict: The object.
    """
    return {
        "object_type": object_type,
        "id": None if id is None else str(id),
        "name": name,
        "status": status,
        "location": location,
        "labels": dict(labels or {}),
        "created": created,
        "vendor_type": vendor_type,
        "properties": {key: value for key, value in properties.items() if value is not None},
    }


# Every vendor's spelling of a CPU architecture, and the one reported for it.
_ARCHITECTURES = {
    "x86": "x86_64",
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "arm": "arm64",
    "arm64": "arm64",
    "aarch64": "arm64",
}


def architecture(value):
    """A vendor's CPU architecture as every vendor reports it: x86_64 or arm64.

    A spelling not known here is passed through as is, and None stays None.
    """
    if value is None:
        return None
    return _ARCHITECTURES.get(str(value).strip().lower(), value)


def as_list(value) -> list:
    """A setting that takes a list, given as a list or as one value."""
    if value in (None, "", []):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def parse_tags(tags) -> dict:
    """Turns the forms a tag filter can be given in into one dict.

    Args:
        tags: A dict of key/value pairs; a list of ``key=value`` or bare
            ``key`` strings; or the same comma-separated in one string. A
            bare key, or a None value, matches any value.

    Returns:
        dict: Key to value, None meaning any value.
    """
    if tags is None or tags == "" or tags == []:
        return {}
    if isinstance(tags, dict):
        return {str(key): None if value is None else str(value) for key, value in tags.items()}
    if isinstance(tags, str):
        tags = tags.split(",")
    if not isinstance(tags, (list, tuple)):
        raise DbaError(
            "tags must be a dict, a list of 'key=value' strings or a comma-separated string."
        )

    parsed = {}
    for tag in tags:
        key, separator, value = str(tag).strip().partition("=")
        if key:
            parsed[key.strip()] = value.strip() if separator else None
    return parsed


def matches_tags(obj: dict, tags: dict) -> bool:
    """Whether an object carries every tag of a filter."""
    labels = obj.get("labels") or {}
    for key, value in tags.items():
        if key not in labels:
            return False
        if value is not None and str(labels[key]) != value:
            return False
    return True


def check_settings(object_type: str, settings, required=("name",), supported=None) -> dict:
    """Checks a deployment's settings and returns them as a dict.

    Args:
        object_type (str): The object type being deployed.
        settings (dict): The settings given.
        required (tuple): The keys that must be given.
        supported (tuple): The keys this vendor supports for the type; None
            for all the standardized ones. The common keys always are.

    Returns:
        dict: The settings, with labels and vendor_settings defaulted to {}.

    Raises:
        DbaError: Settings are not a dict, a required one is missing or one
            is not supported.
    """
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise DbaError("settings must be a dict.")
    settings = dict(settings)

    allowed = set(COMMON_SETTINGS) | set(
        SETTINGS.get(object_type, {}) if supported is None else supported
    )
    unknown = sorted(set(settings) - allowed)
    if unknown:
        raise DbaError(
            f"Unsupported settings for {object_type}: {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(allowed))}."
        )

    missing = [key for key in required if settings.get(key) in (None, "", [])]
    if missing:
        raise DbaError(f"Missing settings for {object_type}: {', '.join(missing)}.")

    for key in ("labels", "vendor_settings"):
        value = settings.get(key)
        if value is None:
            settings[key] = {}
        elif not isinstance(value, dict):
            raise DbaError(f"The setting {key} must be a dict.")

    return settings


class ObjectNotFound(DbaError):
    """There is no deployed object of the name or id."""


class Vendor:
    """The base class of every vendor.

    Class attributes a vendor sets:

    * ``name`` - the vendor's name in every dba function, e.g. "hetzner".
    * ``display_name`` - its name in messages, e.g. "Hetzner Cloud".
    * ``feature_sets`` - the feature sets it implements, from
      :data:`object_types.FEATURE_SETS`.
    * ``native_projects`` - whether its API has projects, so that the
      project picks the API key and nothing else. Otherwise the dba plugin
      labels what it deploys for a project with :data:`PROJECT_LABEL`,
      filters by that label, and lets one key serve every project.
    * ``api_key_help`` - where the user gets an API key, for messages.
    * ``ready_statuses`` - the statuses of an object ready to use, if not
      :data:`READY_STATUSES`.

    Args:
        api_key (str): The API key; None for what needs none.
        project (str): The project, None for the default one.
    """

    name = ""
    display_name = ""
    feature_sets = ()
    native_projects = False
    api_key_help = ""
    ready_statuses = READY_STATUSES

    def __init__(self, api_key: Optional[str] = None, project: Optional[str] = None):
        self.api_key = api_key
        self.project = project
        self._client = None

    # --- What a vendor implements beyond the per-type methods -------------

    def create_client(self):
        """Returns the :class:`RestClient` for the vendor's API, authenticated
        with ``self.api_key``. Implemented by every vendor."""
        raise NotImplementedError

    # --- Shared machinery -------------------------------------------------

    @property
    def client(self):
        """The API client, created on first use and requiring the API key."""
        if self._client is None:
            if not self.api_key:
                raise DbaError(f"No API key for {self.display_name}.")
            self._client = self.create_client()
        return self._client

    def close(self) -> None:
        """Closes the API client, if one was created."""
        if self._client is not None:
            self._client.close()
            self._client = None

    @classmethod
    def object_types(cls) -> list:
        """The standardized object types the vendor supports, in order."""
        return [
            object_type
            for object_type in types.OBJECT_TYPES
            if callable(getattr(cls, f"_list_{object_type}", None))
        ]

    @classmethod
    def object_support(cls) -> list:
        """What the vendor can do with each object type it supports.

        Returns:
            list: One dict per type: ``object_type``, ``description``,
            ``feature_set`` and ``operations`` (from info, list, status,
            deploy, delete).
        """
        support = []
        for object_type in cls.object_types():
            operations = [
                operation
                for operation in ("info", "list", "status", "deploy", "delete")
                if operation == "status"
                or callable(getattr(cls, f"_{operation}_{object_type}", None))
            ]
            support.append(
                {
                    "object_type": object_type,
                    "description": types.OBJECT_TYPES[object_type]["description"],
                    "feature_set": types.OBJECT_TYPES[object_type]["feature_set"],
                    "operations": operations,
                }
            )
        return support

    def _operation(self, operation: str, object_type: str):
        """The method implementing an operation, or a refusal naming why not."""
        object_type = types.check_object_type(object_type)
        supported = self.object_types()
        if object_type not in supported:
            raise DbaError(
                f"{self.display_name} does not support {object_type}. "
                f"It supports: {', '.join(supported) or 'nothing'}."
            )

        method = getattr(self, f"_{operation}_{object_type}", None)
        if not callable(method):
            raise DbaError(
                f"{self.display_name} does not support '{operation}' for "
                f"{object_type}."
            )
        return method

    def require(self, operation: str, object_type: str):
        """The method implementing an operation for a type, or a refusal naming why not.

        Args:
            operation (str): info, list, status, deploy or delete.
            object_type (str): The standardized object type.

        Raises:
            DbaError: The vendor does not support it.
        """
        return self._operation(operation, object_type)

    def object_info(self, object_type: str) -> dict:
        """What of an object type can be deployed. See ``_info_<type>``."""
        return self._operation("info", object_type)()

    def list_objects(self, object_type: str, tags=None) -> list:
        """The deployed objects of a type, filtered by tags and project.

        Args:
            object_type (str): The object type.
            tags: The tag filter, in any form :func:`parse_tags` takes.

        Returns:
            list: The objects, as :func:`make_object` makes them.
        """
        tag_filter = parse_tags(tags)
        if self.project and not self.native_projects:
            tag_filter[PROJECT_LABEL] = self.project

        objects = self._operation("list", object_type)()
        return [obj for obj in objects if matches_tags(obj, tag_filter)]

    def find_object(self, object_type: str, object_name: str) -> dict:
        """The one deployed object of a type with the given name or id.

        A name wins over an id, and two objects of the same name are refused
        rather than one of them picked.

        Raises:
            ObjectNotFound: There is none.
            DbaError: There are more than one.
        """
        if object_name in (None, ""):
            raise DbaError("object_name is empty.")

        objects = self.list_objects(object_type)
        by_name = [obj for obj in objects if obj["name"] == str(object_name)]
        if len(by_name) > 1:
            raise DbaError(
                f"There are {len(by_name)} {object_type} named '{object_name}' at "
                f"{self.display_name}; name one by its id: "
                + ", ".join(obj["id"] for obj in by_name)
                + "."
            )
        if by_name:
            return by_name[0]

        by_id = [obj for obj in objects if obj["id"] == str(object_name)]
        if by_id:
            return by_id[0]

        raise ObjectNotFound(
            f"There is no {object_type} named '{object_name}' at {self.display_name}"
            + (f" in project '{self.project}'" if self.project else "")
            + "."
        )

    def is_ready(self, obj: dict) -> bool:
        """Whether an object's status says it is ready to use."""
        return str(obj.get("status") or "").lower() in self.ready_statuses

    def is_stopped(self, obj: dict) -> bool:
        """Whether an object's status says it exists but does not run."""
        return str(obj.get("status") or "").lower() in STOPPED_STATUSES

    def has_failed(self, obj: dict) -> bool:
        """Whether an object's status says its deployment failed."""
        status = str(obj.get("status") or "").lower()
        return any(word in status for word in FAILED_STATUS_WORDS)

    def object_status(self, object_type: str, object_name: str) -> dict:
        """The current status of one object, by its name or id."""
        self._operation("list", object_type)
        obj = self.find_object(object_type, object_name)
        method = getattr(self, f"_status_{object_type}", None)
        return method(obj) if callable(method) else obj

    def deploy_object(self, object_type: str, settings) -> dict:
        """Deploys one object. See ``_deploy_<type>``.

        For a vendor without projects of its own, the object deployed for a
        named project is labelled with it, so that listing the project
        finds it.
        """
        deploy = self._operation("deploy", object_type)
        settings = dict(settings or {}) if isinstance(settings, dict) else settings
        if isinstance(settings, dict) and self.project and not self.native_projects:
            labels = dict(settings.get("labels") or {})
            labels[PROJECT_LABEL] = self.project
            settings["labels"] = labels
        return deploy(settings)

    def delete_object(self, object_type: str, object_name: str) -> dict:
        """Deletes one object, by its name or id. See ``_delete_<type>``.

        Returns:
            dict: ``deleted`` (the object as it was) and ``result`` (what
            the vendor reported, or None).
        """
        self._operation("delete", object_type)
        return self.delete_found(object_type, self.find_object(object_type, object_name))

    def delete_found(self, object_type: str, obj: dict) -> dict:
        """Deletes an object :meth:`find_object` found. See :meth:`delete_object`."""
        return {"deleted": obj, "result": self._operation("delete", object_type)(obj)}

    def current_status(self, object_type: str, obj: dict) -> dict:
        """The current status of an object found or deployed before, by its id.

        Asks for that one object where the vendor answers for one, instead of
        listing every object of the type, as following a deployment does
        every few seconds.

        Raises:
            ObjectNotFound: It is gone.
        """
        method = getattr(self, f"_status_{types.check_object_type(object_type)}", None)
        if not callable(method):
            return self.find_object(object_type, obj["id"])
        try:
            return method(obj)
        except VendorApiError as e:
            if e.status == 404:
                raise ObjectNotFound(
                    f"There is no {object_type} with the id '{obj['id']}' at {self.display_name}."
                ) from e
            raise

