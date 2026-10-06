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

"""The users of a multi-tenant MCP server.

In multi-tenant mode (``mcp setup --multiTenant=true``) the server serves
several users, each authenticated, each with connections and allowed paths of
their own. This module is the registry of those users and of what belongs to
them:

* **``users.json``**, next to ``settings.json``, maps each user's
  ``mcp_user_id`` - a UUID the plugin generates - to their record: a name, the
  identities they are known by, the scopes they may be granted, their allowed
  paths and whether they are disabled. It holds nothing secret.
* **A shell secret group per user**, named by the ``mcp_user_id``, holds
  everything secret: their connection passwords under the same ``MCP:CONN:``
  keys a single-tenant server uses (see :mod:`mcp_plugin.lib.config`), and
  their API key under :data:`API_KEY_SECRET`.

A user is known by any number of **identities**, each unique across all users:
an ``email`` (compared without regard to case), a ``userId`` of the
administrator's choosing, an ``oauth`` identity (an issuer and the subject it
issued) and a ``mariadb`` account (a server and the account on it). Any of them,
or the UUID itself, names the user to ``mcp.setup``.

The **API key** is stored in plain text, so that an administrator can show it
again (``mcp setup --showApiKey``). That is exactly as safe as the connection
passwords next to it: shell secret groups keep users apart, they do not protect
them from each other - any process running as the server's OS user can read
every group. The server should therefore run under an OS account of its own.

Shell plugin code: errors are raised as ``mysqlsh.Error``.
"""

# cSpell:ignore mysqlsh MariaDB fcntl msvcrt urlsafe

import contextlib
import datetime
import json
import os
import re
import secrets
import uuid
from typing import Optional

import mysqlsh

from mcp_plugin.lib import config, general

# Name of the file inside the plugin data directory that holds the users.
USERS_FILE_NAME = "users.json"

# The version of the users.json format.
_USERS_FILE_VERSION = 1

# The key in settings.json saying whether the server is multi-tenant.
_MULTI_TENANT_SETTING = "multiTenant"

# The secret, in a user's group, holding their API key.
API_KEY_SECRET = "MCP:API_KEY"

# What every API key starts with. It tells a key apart from an OAuth access
# token at a glance, and makes a leaked key easy to find for a secret scanner.
API_KEY_PREFIX = "mdbmcp_"

# The scopes a user can be granted: one per function group a multi-tenant
# server serves. A tool is only callable with the scope of its group.
SCOPE_DB = "mcp:db"
SCOPE_MSM = "mcp:msm"
SUPPORTED_SCOPES = (SCOPE_DB, SCOPE_MSM)

# The scopes a new user gets unless told otherwise.
DEFAULT_SCOPES = SUPPORTED_SCOPES

# The kinds of identity a user can be known by.
IDENTITY_EMAIL = "email"
IDENTITY_USER_ID = "userId"
IDENTITY_OAUTH = "oauth"
IDENTITY_MARIADB = "mariadb"
IDENTITY_TYPES = (IDENTITY_EMAIL, IDENTITY_USER_ID, IDENTITY_OAUTH, IDENTITY_MARIADB)

# Separates the two parts of an oauth (issuer|subject) or mariadb
# (server|account) identity when it is written as one string.
_IDENTITY_PART_SEPARATOR = "|"

# The scope prefix every tool scope has; a scope that does not have it is not
# one this server defines.
_TOOL_SCOPE_PREFIX = "mcp:"

# An API key: the prefix, the user's UUID without dashes, and the random part.
_API_KEY_PATTERN = re.compile(
    rf"^{re.escape(API_KEY_PREFIX)}([0-9a-f]{{32}})_([A-Za-z0-9_-]{{43}})$"
)

# How many random bytes the secret part of an API key holds.
_API_KEY_RANDOM_BYTES = 32


def _shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


def _now() -> str:
    """Returns the current time as an ISO 8601 UTC timestamp."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --- The mode ---------------------------------------------------------------


def is_multi_tenant() -> bool:
    """Returns whether the server is configured to serve several users.

    The persisted setting, as ``mcp setup --multiTenant`` wrote it. A server
    reads it once, when it starts (see :func:`mcp_plugin.lib.server.start`), and
    records it with :func:`mcp_plugin.lib.general.set_multi_tenant`.

    Returns:
        True if multi-tenant mode is turned on.
    """
    return bool(config.get_settings().get(_MULTI_TENANT_SETTING, False))


def set_multi_tenant(enabled: bool) -> None:
    """Turns multi-tenant mode on or off.

    Nothing is moved either way: the single-tenant connections stay in the
    ``generic`` group, where a multi-tenant server does not look, and the users
    stay in ``users.json`` and their groups, where a single-tenant server does
    not look. Switching back restores exactly what was there.

    Args:
        enabled (bool): Whether the server is to be multi-tenant.

    Returns:
        None

    Raises:
        mysqlsh.Error: If turning it on, and the shell has no secret groups.
    """
    if enabled:
        require_secret_groups()

    settings = config.get_settings()
    settings[_MULTI_TENANT_SETTING] = bool(enabled)
    config.save_settings(settings)


def secret_groups_supported() -> bool:
    """Returns whether this shell keeps secrets in groups.

    Probed rather than read off the version: an older shell refuses the
    options argument, and a shell built from a branch may have the feature
    under any version number.

    Returns:
        True if the shell's secret functions take a ``group``.
    """
    try:
        _shell().list_secrets({"group": str(uuid.UUID(int=0))})
    except Exception:  # noqa: BLE001 - any refusal means no groups
        return False

    return True


def require_secret_groups() -> None:
    """Refuses to go on with a shell that cannot keep users' secrets apart.

    Raises:
        mysqlsh.Error: If the shell has no secret groups.
    """
    if not secret_groups_supported():
        raise mysqlsh.Error(
            "Multi-tenant mode needs a MariaDB Shell that keeps secrets in "
            "groups, to keep each user's secrets apart. Upgrade the shell."
        )


# --- users.json ---------------------------------------------------------------


def get_users_file_path() -> str:
    """Returns the full path of the users.json file."""
    return os.path.join(general.get_plugin_data_path(), USERS_FILE_NAME)


def users_file_version() -> Optional[int]:
    """Returns a value that changes whenever users.json is written.

    What a running server compares to notice that ``mcp setup``, in another
    process, removed, disabled or changed a user - one ``stat`` per check, so it
    can be made on every request.

    Returns:
        The file's modification time in nanoseconds, or None if there is no
        file.
    """
    try:
        return os.stat(get_users_file_path()).st_mtime_ns
    except OSError:
        return None


def _users_file_lock():
    """Returns the lock for a read-modify-write of users.json.

    Two writers are possible - ``mcp setup`` in one process and the server in
    another, which links and provisions users at sign-in - and an identity lost
    to a race is not cosmetic (see :func:`mcp_plugin.lib.config.file_lock`).
    """
    return config.file_lock(get_users_file_path())


def read_users() -> dict:
    """Returns every user, as users.json has them.

    Unlike connections.json this file is an access control, so one that cannot
    be read is an error and never quietly no users - although no users is what
    a server makes of it, which refuses everyone.

    Returns:
        A dict of ``mcp_user_id`` to the user's record.

    Raises:
        mysqlsh.Error: If the file exists but cannot be read or is not in the
            expected shape.
    """
    path = get_users_file_path()
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as users_file:
            content = json.load(users_file)
    except (OSError, ValueError) as error:
        raise mysqlsh.Error(f"Could not read the users file '{path}': {error}")

    users = content.get("users") if isinstance(content, dict) else None
    if not isinstance(users, dict):
        raise mysqlsh.Error(
            f"The users file '{path}' is not in the expected format."
        )

    return {
        str(user_id): record
        for user_id, record in users.items()
        if isinstance(record, dict)
    }


def _write_users(users: dict) -> None:
    """Persists every user to users.json, replacing it whole and atomically.

    Args:
        users (dict): What :func:`read_users` returns, changed.

    Returns:
        None
    """
    path = get_users_file_path()
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as users_file:
        json.dump(
            {"version": _USERS_FILE_VERSION, "users": dict(sorted(users.items()))},
            users_file,
            indent=4,
        )
    os.replace(temporary, path)


@contextlib.contextmanager
def _changing_users():
    """Yields the users for a change, and writes them back if it succeeds."""
    with _users_file_lock():
        users = read_users()
        yield users
        _write_users(users)


# --- Identities -----------------------------------------------------------------


def normalize_user_id(value) -> Optional[str]:
    """Returns a value as a canonical ``mcp_user_id``, if it is a UUID at all.

    Args:
        value: The value to read, in any of the forms a UUID is written in.

    Returns:
        The lower-case canonical UUID, or None if the value is not one.
    """
    if not isinstance(value, str):
        return None

    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        return None


def parse_identity(text) -> dict:
    """Returns an identity from the way it is written on the command line.

    ``email:<address>``, ``userId:<id>``, ``oauth:<issuer>|<subject>`` and
    ``mariadb:<server>|<account>``. Without a type, a value holding an ``@`` is
    an email address and anything else a user id - the two an administrator
    types most.

    Args:
        text (str): The identity as written.

    Returns:
        The identity as stored: a dict with a ``type`` and its fields.

    Raises:
        mysqlsh.Error: If it is not a valid identity.
    """
    text = str(text or "").strip()
    if not text:
        raise mysqlsh.Error("An identity cannot be empty.")

    kind, separator, value = text.partition(":")
    if separator and kind in IDENTITY_TYPES:
        value = value.strip()
    elif "@" in text:
        kind, value = IDENTITY_EMAIL, text
    else:
        kind, value = IDENTITY_USER_ID, text

    if not value:
        raise mysqlsh.Error(f"The {kind} identity '{text}' has no value.")

    if kind in (IDENTITY_EMAIL, IDENTITY_USER_ID):
        if kind == IDENTITY_EMAIL and "@" not in value:
            raise mysqlsh.Error(f"'{value}' is not an email address.")

        return {"type": kind, "value": value}

    first, separator, second = value.partition(_IDENTITY_PART_SEPARATOR)
    if not separator or not first.strip() or not second.strip():
        parts = "issuer|subject" if kind == IDENTITY_OAUTH else "server|account"
        raise mysqlsh.Error(
            f"A {kind} identity is written {kind}:<{parts.replace('|', '>|<')}>, "
            f"got '{text}'."
        )

    if kind == IDENTITY_OAUTH:
        return {"type": kind, "issuer": first.strip(), "subject": second.strip()}

    return {"type": kind, "server": first.strip(), "account": second.strip()}


def identity_key(identity: dict) -> tuple:
    """Returns what identifies an identity, in the form identities compare in.

    Args:
        identity (dict): The identity.

    Returns:
        A tuple that is equal for two identities exactly when they name the
        same person: emails without regard to case, everything else as written.
    """
    kind = identity.get("type")
    if kind == IDENTITY_EMAIL:
        return (kind, str(identity.get("value", "")).casefold())
    if kind == IDENTITY_USER_ID:
        return (kind, str(identity.get("value", "")))
    if kind == IDENTITY_OAUTH:
        return (kind, str(identity.get("issuer", "")), str(identity.get("subject", "")))

    return (kind, str(identity.get("server", "")), str(identity.get("account", "")))


def describe_identity(identity: dict) -> str:
    """Returns an identity the way it is written on the command line."""
    kind = identity.get("type")
    if kind in (IDENTITY_EMAIL, IDENTITY_USER_ID):
        return f"{kind}:{identity.get('value', '')}"
    if kind == IDENTITY_OAUTH:
        return f"{kind}:{identity.get('issuer', '')}|{identity.get('subject', '')}"

    return f"{kind}:{identity.get('server', '')}|{identity.get('account', '')}"


def _owner_of(users: dict, identity: dict) -> Optional[str]:
    """Returns the user who has the given identity, if anyone does."""
    wanted = identity_key(identity)
    for user_id, record in users.items():
        for existing in record.get("identities", []):
            if identity_key(existing) == wanted:
                return user_id

    return None


def find_user(identifier) -> Optional[str]:
    """Returns the user an identifier names.

    Args:
        identifier (str): The user's UUID, or any of their identities as
            :func:`parse_identity` reads them.

    Returns:
        The ``mcp_user_id``, or None if nobody is named.
    """
    users = read_users()

    user_id = normalize_user_id(identifier)
    if user_id is not None and user_id in users:
        return user_id

    try:
        identity = parse_identity(identifier)
    except mysqlsh.Error:
        return None

    return _owner_of(users, identity)


def resolve_user(identifier) -> str:
    """Returns the user an identifier names, or fails saying who exists.

    Args:
        identifier (str): The user's UUID, or any of their identities.

    Returns:
        The ``mcp_user_id``.

    Raises:
        mysqlsh.Error: If nobody is named.
    """
    user_id = find_user(identifier)
    if user_id is None:
        raise mysqlsh.Error(
            f"'{identifier}' is not a known user. Use --show --allUsers to "
            "list the users."
        )

    return user_id


def get_user(mcp_user_id) -> Optional[dict]:
    """Returns a user's record, or None if there is no such user."""
    return read_users().get(normalize_user_id(mcp_user_id) or "")


def is_active_user(mcp_user_id) -> bool:
    """Returns whether a user exists and is not disabled."""
    record = get_user(mcp_user_id)

    return record is not None and not record.get("disabled", False)


def describe_user(mcp_user_id, record=None) -> str:
    """Returns a user the way messages name them: name or identity, and id."""
    if record is None:
        record = get_user(mcp_user_id) or {}

    label = record.get("name") or next(
        (describe_identity(i) for i in record.get("identities", [])), ""
    )

    return f"{label} ({mcp_user_id})" if label else str(mcp_user_id)


def normalize_scopes(scopes) -> list:
    """Returns a list of tool scopes, checked.

    Args:
        scopes: The scopes, as a list or a comma-separated string. None means
            :data:`DEFAULT_SCOPES`.

    Returns:
        The scopes, in the order of :data:`SUPPORTED_SCOPES`.

    Raises:
        mysqlsh.Error: If a scope is not one of :data:`SUPPORTED_SCOPES`.
    """
    if scopes is None:
        return list(DEFAULT_SCOPES)

    if isinstance(scopes, str):
        scopes = [scope.strip() for scope in scopes.split(",")]

    wanted = {str(scope).strip() for scope in scopes if str(scope).strip()}
    unknown = sorted(wanted - set(SUPPORTED_SCOPES))
    if unknown:
        raise mysqlsh.Error(
            f"Unknown scope(s): {', '.join(unknown)}. Supported scopes are: "
            f"{', '.join(SUPPORTED_SCOPES)}."
        )

    return [scope for scope in SUPPORTED_SCOPES if scope in wanted]


def add_user(identities, name=None, scopes=None) -> str:
    """Adds a user.

    Args:
        identities (list): The identities the user is known by, as dicts (see
            :func:`parse_identity`). At least one.
        name (str): A name to show for the user, or None.
        scopes: The scopes the user may be granted (see
            :func:`normalize_scopes`).

    Returns:
        The new user's ``mcp_user_id``.

    Raises:
        mysqlsh.Error: If there is no identity, or one already belongs to a
            user.
    """
    require_secret_groups()

    if not identities:
        raise mysqlsh.Error("A user needs at least one identity.")

    granted = normalize_scopes(scopes)

    with _changing_users() as users:
        for identity in identities:
            owner = _owner_of(users, identity)
            if owner is not None:
                raise mysqlsh.Error(
                    f"The identity '{describe_identity(identity)}' already "
                    f"belongs to {describe_user(owner, users[owner])}."
                )

        keys = [identity_key(identity) for identity in identities]
        if len(set(keys)) != len(keys):
            raise mysqlsh.Error("The same identity was given twice.")

        mcp_user_id = str(uuid.uuid4())
        record = {
            "identities": [dict(identity) for identity in identities],
            "scopes": granted,
            "allowedPaths": [],
            "tokenEpoch": 0,
            "disabled": False,
            "created": _now(),
        }
        if name:
            record["name"] = str(name).strip()
        users[mcp_user_id] = record

    return mcp_user_id


def remove_user(mcp_user_id) -> None:
    """Removes a user, and every secret they had.

    The secrets go first, the record after: an interruption in between leaves
    a user with no secrets, who can simply be removed again, rather than
    secrets that belong to nobody.

    Args:
        mcp_user_id (str): The user to remove.

    Returns:
        None
    """
    _shell().delete_all_secrets(*config.secret_options(mcp_user_id))

    with _changing_users() as users:
        users.pop(mcp_user_id, None)


def _change_user(mcp_user_id, change) -> dict:
    """Applies a change to one user's record and persists it.

    Args:
        mcp_user_id (str): The user to change.
        change: A function taking the users dict and the record, changing the
            record in place.

    Returns:
        The changed record.

    Raises:
        mysqlsh.Error: If there is no such user.
    """
    with _changing_users() as users:
        record = users.get(mcp_user_id)
        if record is None:
            raise mysqlsh.Error(f"There is no user '{mcp_user_id}'.")

        change(users, record)

    return record


def add_identity(mcp_user_id, identity: dict) -> None:
    """Adds an identity to a user.

    Raises:
        mysqlsh.Error: If the identity already belongs to a user.
    """

    def change(users, record):
        owner = _owner_of(users, identity)
        if owner == mcp_user_id:
            raise mysqlsh.Error(
                f"{describe_user(mcp_user_id, record)} already has the identity "
                f"'{describe_identity(identity)}'."
            )
        if owner is not None:
            raise mysqlsh.Error(
                f"The identity '{describe_identity(identity)}' already belongs "
                f"to {describe_user(owner, users[owner])}."
            )

        record.setdefault("identities", []).append(dict(identity))

    _change_user(mcp_user_id, change)


def remove_identity(mcp_user_id, identity: dict) -> None:
    """Removes an identity from a user.

    Raises:
        mysqlsh.Error: If the user does not have it, or it is their last one -
            a user nobody can name could only be removed by UUID.
    """

    def change(users, record):
        wanted = identity_key(identity)
        remaining = [
            existing
            for existing in record.get("identities", [])
            if identity_key(existing) != wanted
        ]
        if len(remaining) == len(record.get("identities", [])):
            raise mysqlsh.Error(
                f"{describe_user(mcp_user_id, record)} has no identity "
                f"'{describe_identity(identity)}'."
            )
        if not remaining:
            raise mysqlsh.Error(
                f"'{describe_identity(identity)}' is the only identity of "
                f"{describe_user(mcp_user_id, record)}. Add another first, or "
                "remove the user."
            )

        record["identities"] = remaining

    _change_user(mcp_user_id, change)


def set_disabled(mcp_user_id, disabled: bool) -> None:
    """Disables or enables a user.

    A disabled user keeps everything they have, and is refused by the server
    from the next request on.
    """

    def change(users, record):
        record["disabled"] = bool(disabled)

    _change_user(mcp_user_id, change)


def set_scopes(mcp_user_id, scopes) -> None:
    """Sets the scopes a user may be granted (see :func:`normalize_scopes`)."""
    granted = normalize_scopes(scopes)

    def change(users, record):
        record["scopes"] = granted

    _change_user(mcp_user_id, change)


def get_scopes(mcp_user_id) -> list:
    """Returns the scopes a user may be granted; none for an unknown user."""
    record = get_user(mcp_user_id)
    if record is None:
        return []

    scopes = record.get("scopes")
    if not isinstance(scopes, list):
        return list(DEFAULT_SCOPES)

    return [scope for scope in SUPPORTED_SCOPES if scope in scopes]


def set_default_role(mcp_user_id, role) -> None:
    """Sets the MariaDB role a user's sessions run under; None or "" clears it.

    Applied with ``SET ROLE`` every time one of the user's sessions is opened.
    Without one, the server applies the account's own ``DEFAULT ROLE``.
    """
    role = str(role or "").strip()

    def change(users, record):
        if role:
            record["defaultRole"] = role
        else:
            record.pop("defaultRole", None)

    _change_user(mcp_user_id, change)


def get_default_role(mcp_user_id) -> str:
    """Returns the role a user's sessions run under, or ``""`` for the account's."""
    record = get_user(mcp_user_id) or {}

    return str(record.get("defaultRole") or "")


def revoke_tokens(mcp_user_id) -> int:
    """Invalidates every OAuth access token issued to a user so far.

    The user's ``tokenEpoch`` is raised and the moment recorded: a token
    carrying an older epoch, or issued before that moment, is refused. Their
    API key is not affected - rotate it for that.

    Returns:
        The new epoch.
    """
    epoch = {}

    def change(users, record):
        record["tokenEpoch"] = int(record.get("tokenEpoch", 0)) + 1
        record["tokensRevokedAt"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        epoch["value"] = record["tokenEpoch"]

    _change_user(mcp_user_id, change)

    return epoch["value"]


def link_identity(mcp_user_id, identity: dict) -> bool:
    """Adds an identity to a user unless they already have it.

    For the server's own use when a sign-in links an identity: unlike
    :func:`add_identity` it is not an error for the user to have it already,
    which is what a second sign-in racing the first finds.

    Returns:
        True if it was added.

    Raises:
        mysqlsh.Error: If the identity belongs to another user.
    """
    added = {}

    def change(users, record):
        owner = _owner_of(users, identity)
        if owner == mcp_user_id:
            added["value"] = False
            return
        if owner is not None:
            raise mysqlsh.Error(
                f"The identity '{describe_identity(identity)}' already belongs "
                f"to {describe_user(owner, users[owner])}."
            )
        record.setdefault("identities", []).append(dict(identity))
        added["value"] = True

    _change_user(mcp_user_id, change)

    return added["value"]


def find_user_by_identity(identity: dict) -> Optional[str]:
    """Returns the user who has an identity, or None."""
    return _owner_of(read_users(), identity)


def users_with_email(email) -> list:
    """Returns the users with an email identity matching one address."""
    wanted = identity_key({"type": IDENTITY_EMAIL, "value": email})
    users = read_users()

    return sorted(
        user_id
        for user_id, record in users.items()
        if any(identity_key(i) == wanted for i in record.get("identities", []))
    )


# --- Allowed paths ---------------------------------------------------------------


def get_allowed_paths(mcp_user_id) -> list:
    """Returns the directories one user may access; none for an unknown user."""
    record = get_user(mcp_user_id)
    if record is None:
        return []

    return [path for path in record.get("allowedPaths", []) if isinstance(path, str)]


def set_allowed_paths(mcp_user_id, paths: list) -> None:
    """Sets the directories one user may access."""

    def change(users, record):
        record["allowedPaths"] = list(paths)

    _change_user(mcp_user_id, change)


# --- API keys ----------------------------------------------------------------------


def _new_api_key(mcp_user_id) -> str:
    """Returns a new API key for a user."""
    random_part = secrets.token_urlsafe(_API_KEY_RANDOM_BYTES)

    return f"{API_KEY_PREFIX}{uuid.UUID(mcp_user_id).hex}_{random_part}"


def issue_api_key(mcp_user_id) -> str:
    """Gives a user a new API key, replacing the one they had.

    Args:
        mcp_user_id (str): The user.

    Returns:
        The new key.

    Raises:
        mysqlsh.Error: If there is no such user.
    """
    if get_user(mcp_user_id) is None:
        raise mysqlsh.Error(f"There is no user '{mcp_user_id}'.")

    key = _new_api_key(mcp_user_id)
    _shell().store_secret(API_KEY_SECRET, key, *config.secret_options(mcp_user_id))

    # Written after the key, so the server's cache - flushed whenever this file
    # changes - cannot have re-read the old key in between.
    def change(users, record):
        record["apiKeyCreated"] = _now()

    _change_user(mcp_user_id, change)

    return key


def get_api_key(mcp_user_id) -> Optional[str]:
    """Returns a user's API key, or None if they have none."""
    try:
        return _shell().read_secret(
            API_KEY_SECRET, *config.secret_options(mcp_user_id)
        )
    except Exception:  # noqa: BLE001 - no key is the shell's missing-secret error
        return None


def user_of_api_key(token) -> Optional[str]:
    """Returns the user an API key claims to be for, without checking it.

    Args:
        token (str): A bearer token.

    Returns:
        The ``mcp_user_id`` written into the key, or None if the token is not
        shaped like an API key at all.
    """
    if not isinstance(token, str):
        return None

    match = _API_KEY_PATTERN.match(token)
    if match is None:
        return None

    return str(uuid.UUID(hex=match.group(1)))


def is_api_key(token) -> bool:
    """Returns whether a bearer token is shaped like an API key."""
    return user_of_api_key(token) is not None


# --- Secret groups ---------------------------------------------------------------


def list_groups() -> dict:
    """Returns every secret group that holds anything, with its keys.

    Returns:
        A dict of group UUID to the sorted keys stored in it; the ``generic``
        group is left out.
    """
    groups = {}
    for entry in _shell().list_secrets({"allGroups": True}):
        group = str(entry["group"])
        if group == "generic":
            continue

        groups.setdefault(group, []).append(str(entry["key"]))

    return {group: sorted(keys) for group, keys in sorted(groups.items())}


def orphan_groups() -> list:
    """Returns the secret groups that hold secrets but belong to no user.

    What an interrupted :func:`remove_user` leaves behind - or something else
    that keeps secrets in groups of its own, which is why they are only ever
    reported, and removed only when asked to.

    Returns:
        The sorted group UUIDs.
    """
    users = read_users()

    return [group for group in list_groups() if group not in users]


def purge_group(group) -> None:
    """Deletes every secret in one group."""
    _shell().delete_all_secrets({"group": group})
