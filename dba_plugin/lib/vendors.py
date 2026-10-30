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

"""The vendors the dba plugin knows, and how it finds them.

A vendor plugin is a shell plugin folder named ``dba_vendor_<name>_plugin``
whose ``vendor`` module defines a :class:`dba_plugin.lib.vendor.Vendor`
subclass and passes it to :func:`register`. Its ``init.py`` imports that
module, so the vendor is registered when the shell loads the plugin.

The shell loads plugin folders in whatever order the file system lists them,
and something may ask for the vendors before all of them are loaded - a
test, or a process that imports the library without loading plugins. So
:func:`all_vendors` also imports the ``vendor`` module of every vendor plugin
on ``sys.path`` that has not registered yet. Importing that module registers
the vendor and nothing else: the shell functions are ``init.py``'s, and it
must run once only.
"""

# cSpell:ignore mysqlsh MariaDB

import importlib
import pkgutil
import re

from dba_plugin.lib import object_types as types
from dba_plugin.lib.general import DbaError

VENDOR_PLUGIN_PATTERN = re.compile(r"^dba_vendor_(?P<name>[a-z0-9_]+)_plugin$")

_vendors = {}


def register(vendor_cls):
    """Registers a vendor class, by its ``name``.

    Args:
        vendor_cls: The :class:`Vendor` subclass.

    Returns:
        The class, so that this can decorate it.

    Raises:
        DbaError: The class has no name, an unknown feature set, or a name
            another class already registered.
    """
    name = getattr(vendor_cls, "name", "")
    if not name or not re.fullmatch(r"[a-z0-9_]+", name):
        raise DbaError(f"Vendor class {vendor_cls.__name__} has no valid name.")
    for feature_set in vendor_cls.feature_sets:
        types.check_feature_set(feature_set)

    registered = _vendors.get(name)
    if registered is not None and registered is not vendor_cls:
        # The same module imported under a second name would register an
        # equal class again; a different vendor taking the name is an error.
        if registered.__qualname__ != vendor_cls.__qualname__:
            raise DbaError(f"A vendor named '{name}' is already registered.")
    _vendors[name] = vendor_cls
    return vendor_cls


_discovered = False


def discover() -> None:
    """Imports the vendor module of every vendor plugin not registered yet.

    Scans ``sys.path`` once per process: the plugins are all loaded when the
    shell starts, so a later scan would find nothing new.
    """
    global _discovered

    if _discovered:
        return
    _discovered = True

    for module in pkgutil.iter_modules():
        match = VENDOR_PLUGIN_PATTERN.match(module.name)
        if not match or match.group("name") in _vendors:
            continue
        try:
            importlib.import_module(f"{module.name}.vendor")
        except Exception as e:  # pylint: disable=broad-except
            # One broken vendor plugin must not take the others down; the
            # shell's own load of it reports why.
            _log(f"Could not load the dba vendor plugin {module.name}: {e}")


def _log(message: str) -> None:
    try:
        from mysqlsh.globals import shell

        shell.log("warning", message)
    except Exception:  # pylint: disable=broad-except
        pass


def all_vendors() -> dict:
    """Every vendor class, by name, sorted by name."""
    discover()
    return dict(sorted(_vendors.items()))


def get_vendor(vendor_name: str):
    """The vendor class of a name.

    Raises:
        DbaError: There is no such vendor.
    """
    vendors = all_vendors()
    name = str(vendor_name or "").strip().lower()
    if name in vendors:
        return vendors[name]

    raise DbaError(
        f"Unknown vendor '{vendor_name}'. Available: "
        + (", ".join(vendors) or "none - no dba vendor plugin is installed")
        + "."
    )
