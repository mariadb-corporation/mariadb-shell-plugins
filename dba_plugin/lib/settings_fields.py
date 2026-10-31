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

"""The deployment settings of every object type, described field by field.

A dialog creating or changing a vendor object needs more of a setting than
what it means: a caption, a section to show it in, its type, whether it is
required, its range, the values to choose from and how to show them. Each
standardized setting is described here once, as a field (see :func:`field`).
A vendor adjusts what differs at its API and adds settings of its own with
``Vendor.settings_fields``, and ``Vendor.settings_info`` reports the fields
with the choices the vendor offers.

The same fields check the values given (:func:`field_errors`), without a
request, so that what a dialog validates is what a deployment checks.
"""

# cSpell:ignore mysqlsh MariaDB

import ipaddress
import re

from dba_plugin.lib import object_types as types

# --- Field types ---------------------------------------------------------------

STRING = "string"  # one line of text
TEXT = "text"  # text of several lines: a public key, cloud-init data
INTEGER = "integer"  # a whole number
BOOLEAN = "boolean"
LIST = "list"  # a list of item_type values; one value is taken as a list of one
DICT = "dict"  # keys to values, such as labels
OBJECT_LIST = "object_list"  # a list of dicts, each described by item_fields

FIELD_TYPES = (STRING, TEXT, INTEGER, BOOLEAN, LIST, DICT, OBJECT_LIST)

# The formats a STRING can be required to have.
FORMAT_CIDR = "cidr"  # an IP network, e.g. 10.0.0.0/16
FORMAT_IP_OR_CIDR = "ip_or_cidr"  # an IP address or network
FORMAT_PORT = "port"  # a port, or a range of them: 3306, 4567-4568

# The defaults of the attributes every field reports.
DEFAULTS = {
    "required": False,
    "is_editable": False,
    "advanced": False,
    "vendor_specific": False,
}

# The sections a dialog shows the fields in, in the order they come; a field
# names its section in ``group``. A vendor rewords them in
# ``Vendor.settings_groups``.
GROUPS = {
    "general": {"caption": "General", "description": "What the object is called."},
    "service": {
        "caption": "Service",
        "description": "The database the vendor runs: its topology, version and nodes.",
    },
    "size": {
        "caption": "Type",
        "description": "The size of the object: its resources, and what it costs.",
    },
    "location": {
        "caption": "Location",
        "description": "Where the object runs. Some objects can only be combined with "
        "others in the same location or network zone.",
    },
    "image": {
        "caption": "Image",
        "description": "The operating system the VM is installed with.",
    },
    "networking": {
        "caption": "Networking",
        "description": "How the object is reached: public addresses and private networks.",
    },
    "ssh_keys": {
        "caption": "SSH keys",
        "description": "The SSH keys that may log in. With a key, no password is set.",
    },
    "storage": {
        "caption": "Storage",
        "description": "The storage the object gets, and what it is attached to.",
    },
    "security": {
        "caption": "Security",
        "description": "What may connect to the object: firewalls and allowed addresses.",
    },
    "rules": {"caption": "Rules", "description": "What the object does."},
    "targets": {
        "caption": "Targets",
        "description": "The objects this one applies to.",
    },
    "placement": {
        "caption": "Placement",
        "description": "How the object is placed relative to others, for availability.",
    },
    "labels": {
        "caption": "Labels",
        "description": "Key/value pairs to organize and find objects by.",
    },
    "advanced": {
        "caption": "Advanced",
        "description": "Cloud-init configuration, and what is passed to the vendor's API as is.",
    },
}


def field(caption: str, description: str, type: str = STRING, **attributes) -> dict:  # pylint: disable=redefined-builtin
    """A setting described for a dialog.

    Args:
        caption (str): Its label.
        description (str): What it means: help text for the field.
        type (str): One of :data:`FIELD_TYPES`.
        **attributes: Any of

            * ``group`` - the section of :data:`GROUPS` it is shown in.
            * ``default`` - the value the vendor uses when none is given.
            * ``minimum``, ``maximum`` - the range of an INTEGER.
            * ``min_length``, ``max_length``, ``max_bytes``, ``pattern``,
              ``format`` - what a STRING or TEXT must be (``pattern`` is a
              regular expression the whole value matches).
            * ``key_pattern``, ``value_pattern``, ``key_max_length``,
              ``value_max_length`` - what a DICT's keys and values must be.
            * ``item_type`` - the type of a LIST's items, STRING by default;
              the string rules apply to each item.
            * ``item_fields`` - the fields of an OBJECT_LIST's dicts; with a
              LIST, those of the dicts an item may be instead of a value.
            * ``choices`` - the values to choose from: values, or dicts with
              ``value``, ``caption``, ``description`` and ``details``.
            * ``choices_from`` - where the choices come from at the vendor:
              ``{"info": "<objectInfo key>"}`` - what it offers - or
              ``{"objects": "<object type>"}`` - what is deployed, which a
              dialog can offer to create with that type's own settings.
            * ``strict`` - whether the value must be one of the choices,
              rather than the choices being suggestions.
            * ``filter`` - which choices apply, by other settings: a list of
              ``{"setting", "detail", "selected_detail"}``. A choice applies
              when its ``details[detail]`` is, lists or is listed by the
              value of ``setting`` - or, with ``selected_detail``, by that
              detail of the choice selected for ``setting``. A choice
              without the detail always applies.
            * ``prices_from`` - ``{"info": "<objectInfo key>"}``: what the
              setting costs, reported as ``prices``.
            * ``display`` - how to show the choices: ``style`` (select,
              cards, table, checkboxes), ``columns`` (a table's
              ``{key, caption, unit}``; ``prices.hourly`` and
              ``prices.monthly`` are the price at the selected location),
              ``group_by`` (detail keys to group the choices by) and
              ``item_caption`` (the detail to label a choice with within
              its group).
            * ``is_editable`` - whether the setting of an object already
              deployed can be changed at the vendor.
            * ``advanced`` - whether a dialog may tuck it away.
            * ``vendor_key`` - for a vendor's own setting: where it goes in
              the vendor's request, as a dotted path; the key by default.

    Returns:
        dict: The field.
    """
    return {"caption": caption, "description": description, "type": type, **attributes}


_IN_LOCATION = {"setting": "location", "detail": "location"}

# The columns of a table of VM sizes, whatever the vendor.
SIZE_COLUMNS = [
    {"key": "name", "caption": "Name"},
    {"key": "cores", "caption": "vCPUs"},
    {"key": "memory_gb", "caption": "RAM", "unit": "GB"},
    {"key": "disk_gb", "caption": "Disk", "unit": "GB"},
    {"key": "included_traffic_tb", "caption": "Traffic", "unit": "TB"},
    {"key": "prices.hourly", "caption": "Price / h"},
    {"key": "prices.monthly", "caption": "Price / mo"},
]

COMMON_FIELDS = {
    "name": field("Name", "The object's name. Required.", min_length=1, group="general"),
    "labels": field(
        "Labels",
        "A dict of key/value labels (tags) to put on the object.",
        DICT,
        group="labels",
        advanced=True,
    ),
    "vendor_settings": field(
        "Vendor settings",
        "A dict merged into the vendor's own request body as is.",
        DICT,
        group="advanced",
        advanced=True,
    ),
}

# The standardized settings of each type, in the order a dialog shows them,
# after name and before labels and vendor_settings.
FIELDS = {
    types.MANAGED_MARIADB_SERVICES: {
        "provider": field(
            "Cloud provider",
            "The cloud provider the service runs on, where the vendor offers several.",
            group="location",
            choices_from={"info": "providers"},
            strict=True,
            display={"style": "cards"},
        ),
        "location": field(
            "Region",
            "The region (see object_info 'locations').",
            group="location",
            choices_from={"info": "locations"},
            strict=True,
            filter=[{"setting": "provider", "detail": "provider"}],
        ),
        "topology": field(
            "Topology",
            "The service topology, e.g. 'es-single', 'es-replica', 'galera'.",
            group="service",
            choices_from={"info": "topologies"},
            strict=True,
            display={"style": "cards"},
        ),
        "version": field(
            "Version",
            "The MariaDB server version.",
            group="service",
            choices_from={"info": "versions"},
            strict=True,
            filter=[{"setting": "topology", "detail": "topologies"}],
        ),
        "size": field(
            "Size",
            "The instance size (see object_info 'sizes').",
            group="size",
            choices_from={"info": "sizes"},
            strict=True,
            filter=[
                {"setting": "provider", "detail": "providers"},
                {"setting": "topology", "detail": "topologies"},
            ],
            display={
                "style": "table",
                "columns": [
                    {"key": "name", "caption": "Name"},
                    {"key": "cpu", "caption": "vCPUs"},
                    {"key": "memory", "caption": "RAM"},
                ],
            },
        ),
        "nodes": field("Nodes", "The number of nodes.", INTEGER, group="service", minimum=1),
        "storage_gb": field(
            "Storage", "The storage size in GB.", INTEGER, group="storage", minimum=1
        ),
        "allowed_ips": field(
            "Allowed addresses",
            "A list of IP addresses or CIDR ranges allowed to connect.",
            LIST,
            group="security",
            format=FORMAT_IP_OR_CIDR,
        ),
    },
    types.VMS: {
        "size": field(
            "Type",
            "The VM size, the vendor's server type or plan (see object_info 'sizes').",
            group="size",
            choices_from={"info": "sizes"},
            strict=True,
            filter=[{"setting": "location", "detail": "locations"}],
            display={"style": "table", "columns": SIZE_COLUMNS, "group_by": ["architecture"]},
        ),
        "location": field(
            "Location",
            "The location or zone (see object_info 'locations').",
            group="location",
            choices_from={"info": "locations"},
            strict=True,
            display={"style": "cards", "item_caption": "city"},
        ),
        "image": field(
            "Image",
            "The OS image (see object_info 'images').",
            group="image",
            choices_from={"info": "images"},
            filter=[{"setting": "size", "detail": "architecture", "selected_detail": "architecture"}],
            display={"style": "cards"},
        ),
        "networks": field(
            "Private networks",
            "A list of private network names or ids to attach.",
            LIST,
            group="networking",
            choices_from={"objects": types.NETWORKING},
            display={"style": "checkboxes"},
        ),
        "ssh_keys": field(
            "SSH keys",
            "A list of SSH keys to install: key names or ids where the vendor stores keys "
            "(see ssh_keys), else the public keys themselves.",
            LIST,
            group="ssh_keys",
        ),
        "firewalls": field(
            "Firewalls",
            "A list of firewall names or ids to apply.",
            LIST,
            group="security",
            choices_from={"objects": types.FIREWALLS},
            display={"style": "checkboxes"},
        ),
        "user_data": field(
            "Cloud config",
            "Cloud-init user data.",
            TEXT,
            group="advanced",
            advanced=True,
        ),
    },
    types.VOLUMES: {
        "size_gb": field("Size", "The size in GB.", INTEGER, group="size", minimum=1),
        "location": field(
            "Location",
            "The location or zone; not needed with vm.",
            group="location",
            choices_from={"info": "locations"},
            strict=True,
            display={"style": "cards", "item_caption": "city"},
        ),
        "vm": field(
            "VM",
            "The name or id of a VM to attach the volume to.",
            group="targets",
            choices_from={"objects": types.VMS},
            filter=[_IN_LOCATION],
        ),
        "format": field(
            "Filesystem",
            "The filesystem to format the volume with, where the vendor offers that.",
            group="storage",
            choices_from={"info": "formats"},
            strict=True,
        ),
    },
    types.NETWORKING: {
        "ip_range": field(
            "IP range",
            "The network's IP range in CIDR notation, e.g. 10.0.0.0/16.",
            group="networking",
            format=FORMAT_CIDR,
        ),
        "location": field(
            "Location",
            "The location or network zone.",
            group="location",
            choices_from={"info": "locations"},
            strict=True,
        ),
    },
    types.FIREWALLS: {
        "rules": field(
            "Rules",
            "A list of rules: {direction, protocol, port, source_ips | destination_ips, description}.",
            OBJECT_LIST,
            group="rules",
            item_fields={
                "direction": field(
                    "Direction",
                    "in for incoming, out for outgoing traffic.",
                    choices=["in", "out"],
                    strict=True,
                    default="in",
                ),
                "protocol": field(
                    "Protocol",
                    "The protocol the rule is for.",
                    choices_from={"info": "protocols"},
                    strict=True,
                    required=True,
                ),
                "port": field(
                    "Port", "The port or port range, for tcp and udp.", format=FORMAT_PORT
                ),
                "source_ips": field(
                    "Source addresses",
                    "The IP addresses or CIDR ranges an incoming rule allows.",
                    LIST,
                    format=FORMAT_IP_OR_CIDR,
                ),
                "destination_ips": field(
                    "Destination addresses",
                    "The IP addresses or CIDR ranges an outgoing rule allows.",
                    LIST,
                    format=FORMAT_IP_OR_CIDR,
                ),
                "description": field("Description", "What the rule is for."),
            },
        ),
        "vms": field(
            "Apply to",
            "A list of VM names or ids to apply the firewall to.",
            LIST,
            group="targets",
            choices_from={"objects": types.VMS},
            display={"style": "checkboxes"},
        ),
    },
    types.LOAD_BALANCERS: {
        "size": field(
            "Type",
            "The load balancer type (see object_info 'sizes').",
            group="size",
            choices_from={"info": "sizes"},
            strict=True,
            display={"style": "table"},
        ),
        "location": field(
            "Location",
            "The location or zone.",
            group="location",
            choices_from={"info": "locations"},
            strict=True,
            display={"style": "cards", "item_caption": "city"},
        ),
        "network": field(
            "Private network",
            "The name or id of a private network to attach it to.",
            group="networking",
            choices_from={"objects": types.NETWORKING},
        ),
        "targets": field(
            "Targets",
            "A list of VM names or ids to balance across.",
            LIST,
            group="targets",
            choices_from={"objects": types.VMS},
            display={"style": "checkboxes"},
        ),
        "services": field(
            "Services",
            "A list of {protocol, listen_port, destination_port}.",
            OBJECT_LIST,
            group="rules",
            item_fields={
                "protocol": field("Protocol", "The protocol balanced.", default="tcp"),
                "listen_port": field(
                    "Listen port",
                    "The port the load balancer listens on.",
                    INTEGER,
                    minimum=1,
                    maximum=65535,
                    required=True,
                ),
                "destination_port": field(
                    "Destination port",
                    "The port on the targets; the listen port by default.",
                    INTEGER,
                    minimum=1,
                    maximum=65535,
                ),
            },
        ),
    },
    types.DNS: {
        "ttl": field(
            "TTL", "The zone's default TTL in seconds.", INTEGER, group="rules", minimum=1
        ),
        "records": field(
            "Records",
            "A list of {name, type, value, ttl} records to create.",
            OBJECT_LIST,
            group="rules",
            item_fields={
                "name": field("Name", "The record's name; @ for the zone itself.", default="@"),
                "type": field(
                    "Type",
                    "The record type.",
                    choices_from={"info": "record_types"},
                    strict=True,
                    required=True,
                ),
                "value": field("Value", "The record's value.", required=True),
                "ttl": field("TTL", "The record's TTL in seconds.", INTEGER, minimum=1),
            },
        ),
    },
    types.OBJECT_STORAGE: {
        "location": field(
            "Region",
            "The region.",
            group="location",
            choices_from={"info": "locations"},
            strict=True,
        ),
        "size": field(
            "Size",
            "The storage size or tier, where the vendor sells it in sizes.",
            group="size",
        ),
    },
    types.SSH_KEYS: {
        "public_key": field(
            "Public key",
            "The public key, in OpenSSH format. Required.",
            TEXT,
            group="general",
        ),
    },
}


def descriptions(object_type: str) -> dict:
    """The standardized settings of a type, each with what it means."""
    return {
        key: details["description"]
        for key, details in {**COMMON_FIELDS, **FIELDS.get(object_type, {})}.items()
    }


# --- Checking values ---------------------------------------------------------------

_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")
_PORT = re.compile(r"\d{1,5}(-\d{1,5})?")


def as_int(value):
    """A whole number given as a number or as text, or None if it is none."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == int(value) else None
    if isinstance(value, str) and re.fullmatch(r"\s*[-+]?\d+\s*", value):
        return int(value)
    return None


def as_bool(value):
    """A yes or no given as a boolean or as text, or None if it is neither."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
        return value.strip().lower() in _TRUE
    return None


def converted(details: dict, value):
    """A value as the vendor's API takes it: a whole number or a boolean as one."""
    kind = details.get("type", STRING)
    if kind == INTEGER and as_int(value) is not None:
        return as_int(value)
    if kind == BOOLEAN and as_bool(value) is not None:
        return as_bool(value)
    return value


def choice_values(choices) -> list:
    """The values of a field's choices, as text."""
    return [str(choice["value"] if isinstance(choice, dict) else choice) for choice in choices or []]


def _format_error(name: str, text: str, format_name: str):
    if format_name in (FORMAT_CIDR, FORMAT_IP_OR_CIDR):
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            network = None
        if network is None or (format_name == FORMAT_CIDR and "/" not in text):
            what = "a CIDR range, e.g. 10.0.0.0/16" if format_name == FORMAT_CIDR else "an IP address or CIDR range"
            return f"The setting {name} must be {what}, not '{text}'."
    elif format_name == FORMAT_PORT:
        ports = [int(port) for port in text.split("-")] if _PORT.fullmatch(text) else []
        if not ports or not all(1 <= port <= 65535 for port in ports) or ports != sorted(ports):
            return f"The setting {name} must be a port or a range of ports, e.g. 3306 or 4567-4568, not '{text}'."
    return None


def _scalar_errors(name: str, details: dict, value, kind: str) -> list:
    """What is wrong with one value of a STRING, TEXT, INTEGER or BOOLEAN."""
    if kind == INTEGER:
        number = as_int(value)
        if number is None:
            return [f"The setting {name} must be a whole number, not '{value}'."]
        if details.get("minimum") is not None and number < details["minimum"]:
            return [f"The setting {name} must be at least {details['minimum']}."]
        if details.get("maximum") is not None and number > details["maximum"]:
            return [f"The setting {name} must be at most {details['maximum']}."]
        return []

    if kind == BOOLEAN:
        return [] if as_bool(value) is not None else [f"The setting {name} must be true or false, not '{value}'."]

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return [f"The setting {name} must be text."]
    text = str(value)
    if kind == STRING:
        text = text.strip()
    if details.get("min_length") is not None and len(text) < details["min_length"]:
        return [
            f"The setting {name} must not be empty." if details["min_length"] == 1
            else f"The setting {name} must have at least {details['min_length']} characters."
        ]
    if details.get("max_length") is not None and len(text) > details["max_length"]:
        return [f"The setting {name} must have at most {details['max_length']} characters."]
    if details.get("max_bytes") is not None and len(text.encode("utf-8")) > details["max_bytes"]:
        return [f"The setting {name} must have at most {details['max_bytes']} bytes."]
    if details.get("pattern") and text and not re.fullmatch(details["pattern"], text):
        return [f"The setting {name} has the wrong form: '{text}'."]
    if details.get("format") and text:
        error = _format_error(name, text, details["format"])
        if error:
            return [error]
    choices = details.get("choices")
    if details.get("strict") and choices and text.lower() not in [c.lower() for c in choice_values(choices)]:
        return [f"The setting {name} must be one of: {', '.join(choice_values(choices))}; not '{text}'."]
    return []


def _dict_errors(name: str, details: dict, value) -> list:
    if not isinstance(value, dict):
        return [f"The setting {name} must be a dict."]
    errors = []
    for key, item in value.items():
        key = str(key)
        if details.get("key_max_length") and len(key) > details["key_max_length"]:
            errors.append(f"The key '{key}' of {name} must have at most {details['key_max_length']} characters.")
        elif details.get("key_pattern") and not re.fullmatch(details["key_pattern"], key):
            errors.append(f"The key '{key}' of {name} has the wrong form.")
        text = "" if item is None else str(item)
        if details.get("value_max_length") and len(text) > details["value_max_length"]:
            errors.append(
                f"The value of '{key}' in {name} must have at most {details['value_max_length']} characters."
            )
        elif details.get("value_pattern") and not re.fullmatch(details["value_pattern"], text):
            errors.append(f"The value of '{key}' in {name} has the wrong form: '{text}'.")
    return errors


def field_errors(name: str, details: dict, value) -> list:
    """What is wrong with the value of a setting, without asking the vendor.

    Args:
        name (str): The setting's key, as messages name it.
        details (dict): Its field.
        value: Its value. None is no value: whether one is required is
            checked with the settings as a whole.

    Returns:
        list: The messages; empty if nothing is wrong.
    """
    if value is None:
        return []
    kind = details.get("type", STRING)

    if kind == DICT:
        return _dict_errors(name, details, value)

    if kind == LIST:
        if isinstance(value, dict):
            return [f"The setting {name} must be a list."]
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        item_kind = details.get("item_type", STRING)
        errors = []
        for index, item in enumerate(items):
            if isinstance(item, dict) and details.get("item_fields"):
                errors.extend(_item_errors(f"{name}[{index}]", details, item))
            else:
                errors.extend(_scalar_errors(f"{name}[{index}]", details, item, item_kind))
        return errors

    if kind == OBJECT_LIST:
        if not isinstance(value, (list, tuple)):
            return [f"The setting {name} must be a list."]
        errors = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"The setting {name}[{index}] must be a dict.")
            else:
                errors.extend(_item_errors(f"{name}[{index}]", details, item))
        return errors

    return _scalar_errors(name, details, value, kind)


def _item_errors(name: str, details: dict, item: dict) -> list:
    """What is wrong with one dict of a list, by the list's item_fields."""
    errors = []
    for key, item_field in (details.get("item_fields") or {}).items():
        if item_field.get("required") and item.get(key) in (None, "", []):
            errors.append(f"The setting {name} needs {key}.")
        else:
            errors.extend(field_errors(f"{name}.{key}", item_field, item.get(key)))
    return errors
