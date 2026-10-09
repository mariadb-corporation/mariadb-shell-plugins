# Copyright (c) 2022, 2026, Oracle and/or its affiliates.
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
import mrs_plugin.lib as lib


def _as_id(query):
    """Returns the canonical id when a query names an object by id, else None.

    Ids used to be bytes and were told apart from a path by their type. They
    are UUID strings now (or their '0x' and base64 forms), so a query is an id
    exactly when it parses as one; anything else is a 'host/path' query.
    """
    if query is None or isinstance(query, str) and "/" in query:
        return None
    try:
        return lib.core.id_to_uuid(query, "id")
    except RuntimeError:
        return None


def resolve_service(
    session,
    service_query: str | bytes = None,
    required: bool = True,
    auto_select_single: bool = False,
):
    service = None
    if service_query:
        service_id = _as_id(service_query)
        if service_id is not None:
            # Check if given service exists by searching its id
            service = lib.services.get_service(service_id=service_id, session=session)
        else:
            # Check if the service exists by host and context root
            url_host_name, url_context_root = service_query.split("/", 1)
            url_context_root = f"/{url_context_root}"
            service = lib.services.get_service(
                url_host_name=url_host_name,
                url_context_root=url_context_root,
                session=session,
            )
        if not service and required:
            raise Exception("Operation cancelled. Unable to identify target service.")

    if not service:
        service = lib.services.get_current_service(session)

    if not service:
        services = lib.services.get_services(session)
        if len(services) == 1 and auto_select_single:
            # If there only is one service and auto_select_single is True, take the single service
            service = services[0]

    if not service and lib.core.get_interactive_default():
        print("MRS - Service Listing\n")

        service = lib.core.prompt_for_list_item(
            item_list=services,
            prompt_caption=(
                "Please select a service index or type " "'hostname/root_context'"
            ),
            item_name_property="host_ctx",
            given_value=None,
            print_list=True,
        )

    if not service and required:
        raise Exception("Operation cancelled. Unable to identify target service.")

    return service
