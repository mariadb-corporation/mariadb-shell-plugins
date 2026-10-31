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
  :func:`make_object`. The dba plugin filters by tags and project itself. A
  vendor whose API filters by labels takes ``labels=None`` - a dict of key
  to value, None for any value - and asks for those only.
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

import concurrent.futures
import copy
import inspect
from typing import Optional

from dba_plugin.lib import object_types as types, settings_fields as sf
from dba_plugin.lib.general import DbaError, SettingError
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

# The state every object reports next to its vendor's status, in the same
# words for every vendor, so that a client can tell what is ready to use, on
# its way, stopped, going or broken without knowing each vendor's wording.
STATE_READY = "ready"
STATE_PENDING = "pending"
STATE_STOPPED = "stopped"
STATE_DELETING = "deleting"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"


def state_of(status) -> str:
    """The standardized state of a vendor's status.

    Ready, stopped and failed statuses are known (:data:`READY_STATUSES`,
    :data:`STOPPED_STATUSES`, :data:`FAILED_STATUS_WORDS`); one naming a
    deletion is deleting; any other status - creating, starting,
    maintenance, pending_create - is an object on its way: pending.
    """
    if status in (None, ""):
        return STATE_UNKNOWN
    status = str(status).lower()
    if any(word in status for word in FAILED_STATUS_WORDS):
        return STATE_FAILED
    if status in READY_STATUSES:
        return STATE_READY
    if status in STOPPED_STATUSES:
        return STATE_STOPPED
    if "delet" in status:
        return STATE_DELETING
    return STATE_PENDING

# The standardized settings keys per object type, with what each means. A
# vendor maps the ones it supports onto its API, refuses those it does not
# and passes ``vendor_settings`` through. Each is described for a dialog in
# :mod:`dba_plugin.lib.settings_fields`.
COMMON_SETTINGS = {key: field["description"] for key, field in sf.COMMON_FIELDS.items()}
SETTINGS = {
    object_type: {key: field["description"] for key, field in fields.items()}
    for object_type, fields in sf.FIELDS.items()
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
            STATUS_ACTIVE for an object the vendor gives no state. The
            object reports it as ``status``, and its :func:`state_of` as
            ``state``.
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
        "state": state_of(status),
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


# A stop for a listing that never ends.
MAX_PAGES = 100


def paginate(fetch, max_pages: int = MAX_PAGES) -> list:
    """Every item of a paged listing, whatever the vendor pages it by.

    Args:
        fetch: Called with the cursor of a page - None for the first - and
            returning its items and the next page's cursor: a page number,
            an offset or a token; None after the last page.
        max_pages (int): A stop for a listing that never ends.

    Returns:
        list: The items of every page.
    """
    items = []
    cursor = None
    seen = set()
    for _ in range(max_pages):
        batch, cursor = fetch(cursor)
        items.extend(batch or [])
        # a cursor seen before would page in a circle
        if cursor is None or cursor in seen:
            break
        seen.add(cursor)
    return items


def parallel(*calls) -> list:
    """Runs independent calls - requests to one vendor - side by side.

    Returns:
        list: Their results, in the order of the calls.

    Raises:
        Exception: What the first failing call raised.
    """
    if len(calls) < 2:
        return [call() for call in calls]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(call) for call in calls]
        return [future.result() for future in futures]


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


def setting_error(message: str, settings=()) -> dict:
    """A problem with deployment settings, as a validation reports it:
    ``message``, and ``settings`` - the keys it is about."""
    return {"settings": [settings] if isinstance(settings, str) else list(settings), "message": message}


def settings_problems(object_type: str, settings, required=("name",), supported=None) -> tuple:
    """What is wrong with a deployment's settings as a whole.

    The arguments are those of :func:`check_settings`.

    Returns:
        tuple: The settings, as :func:`check_settings` returns them, or None
        if they are no dict; and the problems, each a :func:`setting_error`,
        in the order :func:`check_settings` refuses them.
    """
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        return None, [setting_error("settings must be a dict.")]
    settings = dict(settings)
    errors = []

    allowed = set(COMMON_SETTINGS) | set(
        SETTINGS.get(object_type, {}) if supported is None else supported
    )
    unknown = sorted(set(settings) - allowed)
    if unknown:
        errors.append(
            setting_error(
                f"Unsupported settings for {object_type}: {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(allowed))}.",
                unknown,
            )
        )

    missing = [key for key in required if settings.get(key) in (None, "", [])]
    if missing:
        errors.append(setting_error(f"Missing settings for {object_type}: {', '.join(missing)}.", missing))

    for key in ("labels", "vendor_settings"):
        value = settings.get(key)
        if value is None:
            settings[key] = {}
        elif not isinstance(value, dict):
            errors.append(setting_error(f"The setting {key} must be a dict.", key))

    return settings, errors


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
        SettingError: Settings are not a dict, a required one is missing or
            one is not supported.
    """
    settings, errors = settings_problems(object_type, settings, required, supported)
    if errors:
        raise SettingError(errors[0]["message"], errors[0]["settings"])
    return settings


def _put(target: dict, path: str, value) -> None:
    """Sets a value in a nested dict at a dotted path, making the dicts on the way."""
    *parents, last = path.split(".")
    for key in parents:
        child = target.get(key)
        target[key] = child = dict(child) if isinstance(child, dict) else {}
        target = child
    target[last] = value


def _choice(item) -> dict:
    """An entry of what a vendor offers, as a field's choice.

    The value is the entry's ``value``, else its ``name``; what else the
    entry says - architecture, cores, prices, the locations it is offered
    in - becomes its ``details``.
    """
    if not isinstance(item, dict):
        return {"value": item, "caption": str(item), "description": None, "details": {}}
    value = item.get("value", item.get("name"))
    return {
        "value": value,
        "caption": str(item.get("name") if item.get("name") is not None else value),
        "description": item.get("description"),
        "details": {
            key: detail for key, detail in item.items() if key not in ("value", "name", "description")
        },
    }


def _object_choice(obj: dict) -> dict:
    """A deployed object, as a choice: its name, else its id."""
    return {
        "value": obj.get("name") or obj.get("id"),
        "caption": obj.get("name") or obj.get("id"),
        "description": obj.get("vendor_type"),
        "details": {
            "id": obj.get("id"),
            "location": obj.get("location"),
            "status": obj.get("status"),
            "state": obj.get("state"),
            "labels": obj.get("labels"),
        },
    }


def _same(detail, wanted) -> bool:
    """Whether a choice's detail is, lists or is listed by a wanted value."""
    details = [str(item).lower() for item in (detail if isinstance(detail, (list, tuple)) else [detail])]
    wanted_values = [str(item).lower() for item in (wanted if isinstance(wanted, (list, tuple)) else [wanted])]
    return any(item in details for item in wanted_values)


def _matching(choices: list, value) -> Optional[dict]:
    """The choice a value names, by its value or its id."""
    text = str(value).lower()
    for choice in choices:
        if str(choice["value"]).lower() == text or str(choice["details"].get("id", "")).lower() == text:
            return choice
    return None


def applying_choices(fields: dict, key: str, settings: dict) -> list:
    """The choices of a field that apply with the other settings given.

    See ``filter`` in :func:`settings_fields.field`.
    """
    details = fields[key]
    choices = details.get("choices") or []
    for rule in details.get("filter") or ():
        given = settings.get(rule["setting"])
        if given in (None, "", []):
            continue
        other = fields.get(rule["setting"]) or {}
        selected = _matching(other.get("choices") or [], given)
        if other.get("choices") and selected is None:
            continue  # a value not offered is no reason to rule choices out
        wanted = given
        if rule.get("selected_detail"):
            wanted = None if selected is None else selected["details"].get(rule["selected_detail"])
            if wanted is None:
                continue
        choices = [
            choice
            for choice in choices
            if choice["details"].get(rule["detail"]) in (None, "", [])
            or _same(choice["details"][rule["detail"]], wanted)
        ]
    return choices


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
    * ``deploy_settings`` - the settings each type requires and supports,
      checked by :meth:`check_deploy_settings` with what ``_check_<type>``
      adds, before a deployment's task starts.
    * ``settings_fields`` - how the vendor's settings differ from the
      standardized fields of :mod:`settings_fields`, per type: a standardized
      key maps to the attributes it changes - a range, the choices'
      source, ``is_editable`` - and a new key adds a setting of the vendor's
      own, a :func:`settings_fields.field` that goes into the request at its
      ``vendor_key``.
    * ``settings_order`` - per type, the keys a dialog shows first, in that
      order, where the vendor's own console orders them differently.
    * ``settings_groups`` - the dialog sections the vendor words
      differently: ``{group: {"caption", "description"}}``.

    Args:
        api_key (str): The API key; None for what needs none.
        project (str): The project, None for the default one.
    """

    name = ""
    display_name = ""
    feature_sets = ()
    native_projects = False
    api_key_help = ""
    # The settings each type's deployment requires and supports, beyond the
    # common ones, as {object_type: {"required": (...), "supported": (...)}};
    # a type not named requires a name and takes every standardized setting.
    deploy_settings = {}
    settings_fields = {}
    settings_order = {}
    settings_groups = {}

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

    @classmethod
    def fields(cls, object_type: str) -> dict:
        """The fields of a type's deployment settings at this vendor, without a request.

        Name, the standardized settings the vendor supports, its own, then
        labels and vendor_settings - unless ``settings_order`` puts some
        first. Each field is a :func:`settings_fields.field` with ``key``,
        ``position`` (from 1), ``required``, ``is_editable``, ``advanced``,
        ``vendor_specific`` and ``group``; static choices as dicts.

        Returns:
            dict: The fields by key, in their order.
        """
        object_type = types.check_object_type(object_type)
        rules = cls.deploy_settings.get(object_type, {})
        required = rules.get("required", ("name",))
        supported = rules.get("supported")
        standard = sf.FIELDS.get(object_type, {})
        changes = cls.settings_fields.get(object_type, {})
        own = {
            key: {**details, "vendor_specific": True, "vendor_key": details.get("vendor_key", key)}
            for key, details in changes.items()
            if key not in sf.COMMON_FIELDS and key not in standard
        }

        ordered = {"name": sf.COMMON_FIELDS["name"]}
        ordered.update(
            (key, details) for key, details in standard.items() if supported is None or key in supported
        )
        ordered.update(own)
        ordered["labels"] = sf.COMMON_FIELDS["labels"]
        ordered["vendor_settings"] = sf.COMMON_FIELDS["vendor_settings"]
        first = [key for key in cls.settings_order.get(object_type, ()) if key in ordered]
        keys = first + [key for key in ordered if key not in first]

        fields = {}
        for position, key in enumerate(keys, 1):
            details = copy.deepcopy(
                {**sf.DEFAULTS, "group": "general", **ordered[key], **({} if key in own else changes.get(key, {}))}
            )
            details["required"] = key in required or bool(details["required"])
            if details.get("choices"):
                details["choices"] = [_choice(choice) for choice in details["choices"]]
            for item in (details.get("item_fields") or {}).values():
                if item.get("choices"):
                    item["choices"] = [_choice(choice) for choice in item["choices"]]
            fields[key] = {"key": key, "position": position, **details}
        return fields

    def settings_problems(self, object_type: str, settings) -> tuple:
        """What is wrong with a deployment's settings, as far as is known without the vendor's API.

        The settings as a whole (:func:`settings_problems`), each value by
        its field (:func:`settings_fields.field_errors`), then - when that
        found nothing - ``_check_<type>(settings)``, where the vendor has
        more rules that need no request: a combination, a format. The
        vendor's own settings are moved into ``vendor_settings`` at their
        ``vendor_key``, as its API takes them.

        Returns:
            tuple: The settings, as :meth:`check_deploy_settings` returns
            them, or None if they are no dict; and the problems, each a
            :func:`setting_error`.
        """
        object_type = types.check_object_type(object_type)
        fields = self.fields(object_type)
        rules = self.deploy_settings.get(object_type, {})
        own = {key: details for key, details in fields.items() if details["vendor_specific"]}
        given = dict(settings) if isinstance(settings, dict) else settings
        moved = {key: given.pop(key) for key in own if key in given} if isinstance(given, dict) else {}

        checked, errors = settings_problems(
            object_type, given, required=rules.get("required", ("name",)), supported=rules.get("supported")
        )
        if checked is None:
            return None, errors

        for key, value in list(checked.items()) + list(moved.items()):
            if key in fields and not any(key in error["settings"] for error in errors):
                errors.extend(
                    setting_error(message, key) for message in sf.field_errors(key, fields[key], value)
                )
        if moved and isinstance(checked["vendor_settings"], dict):
            vendor_settings = dict(checked["vendor_settings"])
            for key, value in moved.items():
                if value is not None:
                    _put(vendor_settings, own[key]["vendor_key"], sf.converted(own[key], value))
            checked["vendor_settings"] = vendor_settings

        more = getattr(self, f"_check_{object_type}", None)
        if not errors and callable(more):
            try:
                more(checked)
            except SettingError as e:
                errors.append(setting_error(str(e), e.settings))
            except DbaError as e:
                errors.append(setting_error(str(e)))
        return checked, errors

    def check_deploy_settings(self, object_type: str, settings) -> dict:
        """Checks a deployment's settings as far as it can without the vendor's API.

        See :meth:`settings_problems`. Called before a deployment's task
        starts, so that a mistake fails the call, and by ``_deploy_<type>``
        itself.

        Returns:
            dict: The settings, as :func:`check_settings` returns them, with
            the vendor's own settings moved into ``vendor_settings``.

        Raises:
            SettingError: A setting is missing, not supported or not valid;
                the first problem found.
        """
        settings, errors = self.settings_problems(object_type, settings)
        if errors:
            raise SettingError(errors[0]["message"], errors[0]["settings"])
        return settings

    def _fill_choices(self, object_type: str, fields: dict) -> None:
        """Gives the fields - and their items' fields - the choices the vendor offers.

        What the vendor offers to deploy (``_info_<type>``) and what is
        deployed of each type the choices name are asked for side by side,
        once each. A source the vendor does not have leaves its field
        without choices.
        """
        everything = list(fields.values()) + [
            item for details in fields.values() for item in (details.get("item_fields") or {}).values()
        ]
        wants_info = any(
            "info" in (details.get("choices_from") or {}) or details.get("prices_from") for details in everything
        )
        listed = sorted(
            {
                details["choices_from"]["objects"]
                for details in everything
                if "objects" in (details.get("choices_from") or {})
            }
            & set(self.object_types())
        )
        calls = []
        if wants_info and callable(getattr(self, f"_info_{object_type}", None)):
            calls.append(lambda: self.object_info(object_type))
        calls += [lambda listed_type=listed_type: self.list_objects(listed_type) for listed_type in listed]
        answers = parallel(*calls) if calls else []
        info = answers.pop(0) if len(calls) > len(listed) else {}
        deployed = dict(zip(listed, answers))

        for details in everything:
            source = details.get("choices_from") or {}
            if source.get("info") in (info or {}):
                details["choices"] = [_choice(item) for item in as_list(info[source["info"]])]
            elif source.get("objects") in deployed:
                details["choices"] = [_object_choice(obj) for obj in deployed[source["objects"]]]
            prices = (details.get("prices_from") or {}).get("info")
            if prices in (info or {}):
                details["prices"] = info[prices]

    def settings_info(self, object_type: str) -> dict:
        """The settings of a type's deployment, described for a dialog.

        The fields of :meth:`fields`, with the choices the vendor offers and
        the objects deployed to choose from, and the sections to show them
        in.

        Returns:
            dict: ``vendor``, ``display_name``, ``object_type``, ``groups``
            (``key``, ``caption``, ``description`` and ``position`` of each
            section the fields name, in order) and ``settings`` (the fields
            by key, in order).

        Raises:
            DbaError: The vendor does not deploy the type, or what it offers
                could not be asked for.
        """
        object_type = types.check_object_type(object_type)
        self.require("deploy", object_type)
        fields = self.fields(object_type)
        self._fill_choices(object_type, fields)

        groups = []
        for details in fields.values():
            if details["group"] not in [group["key"] for group in groups]:
                wording = {**sf.GROUPS.get(details["group"], {}), **self.settings_groups.get(details["group"], {})}
                groups.append(
                    {
                        "key": details["group"],
                        "caption": wording.get("caption", details["group"]),
                        "description": wording.get("description"),
                        "position": len(groups) + 1,
                    }
                )
        return {
            "vendor": self.name,
            "display_name": self.display_name,
            "object_type": object_type,
            "groups": groups,
            "settings": fields,
        }

    def validate_settings(self, object_type: str, settings) -> dict:
        """Checks a deployment's settings, against what the vendor offers too.

        What :meth:`settings_problems` finds, and - when that is nothing -
        each value with choices against them: a size, location or version
        the vendor does not offer, or not with the other settings given
        (another provider, another location), is an error; a value of a
        setting whose choices are suggestions - an image given by its id -
        is a warning. An object named that is not deployed is an error;
        only a warning where listing the project may not show it, at a
        vendor without projects of its own.

        Returns:
            dict: ``valid``, ``errors`` and ``warnings``, each problem a
            :func:`setting_error`.

        Raises:
            DbaError: The vendor does not deploy the type, or what it offers
                could not be asked for.
        """
        object_type = types.check_object_type(object_type)
        self.require("deploy", object_type)
        checked, errors = self.settings_problems(object_type, settings)
        warnings = []
        if checked is not None and not errors:
            fields = {
                key: details
                for key, details in self.fields(object_type).items()
                if details.get("choices_from") or details.get("choices")
            }
            given = {key: value for key, value in settings.items() if value not in (None, "", [])}
            self._fill_choices(object_type, {key: details for key, details in fields.items() if key in given})
            for key in [key for key in fields if key in given and fields[key].get("choices") is not None]:
                self._check_choices(fields, key, given, errors, warnings)
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    def _check_choices(self, fields, key, given, errors, warnings) -> None:
        details = fields[key]
        applying = applying_choices(fields, key, given)
        objects_type = (details.get("choices_from") or {}).get("objects")
        values = as_list(given[key]) if details["type"] == sf.LIST else [given[key]]
        others = [rule["setting"] for rule in details.get("filter") or () if given.get(rule["setting"]) not in (None, "", [])]
        with_others = " and ".join(f"{other} '{given[other]}'" for other in others)
        for value in values:
            if _matching(applying, value):
                continue
            if _matching(details["choices"], value):
                errors.append(
                    setting_error(
                        f"The {key} '{value}' cannot be used with {with_others}." if objects_type
                        else f"{self.display_name} does not offer the {key} '{value}' with {with_others}.",
                        [key] + others,
                    )
                )
            elif objects_type:
                message = f"There is no {objects_type} named '{value}' at {self.display_name}" + (
                    f" in project '{self.project}'." if self.project else "."
                )
                hidden = self.project and not self.native_projects
                (warnings if hidden else errors).append(setting_error(message, key))
            elif details.get("strict"):
                offered = sf.choice_values(applying)
                errors.append(
                    setting_error(
                        f"{self.display_name} does not offer the {key} '{value}'. "
                        + (f"Choose one of: {', '.join(offered[:20])}" + (", ..." if len(offered) > 20 else "") + "."
                           if offered else "It offers none."),
                        key,
                    )
                )
            else:
                warnings.append(
                    setting_error(
                        f"{self.display_name} does not list the {key} '{value}'; the deployment fails "
                        "if it does not take it.",
                        key,
                    )
                )

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

        list_type = self._operation("list", object_type)
        # A vendor that can filter by labels on its side says so with a
        # labels parameter; it gets the filter, and what it answers is
        # filtered here all the same, whatever it made of it.
        if tag_filter and "labels" in inspect.signature(list_type).parameters:
            objects = list_type(labels=tag_filter)
        else:
            objects = list_type()
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

    @staticmethod
    def is_ready(obj: dict) -> bool:
        """Whether an object's status says it is ready to use."""
        return state_of(obj.get("status")) == STATE_READY

    @staticmethod
    def is_stopped(obj: dict) -> bool:
        """Whether an object's status says it exists but does not run."""
        return state_of(obj.get("status")) == STATE_STOPPED

    @staticmethod
    def has_failed(obj: dict) -> bool:
        """Whether an object's status says its deployment failed."""
        return state_of(obj.get("status")) == STATE_FAILED

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

