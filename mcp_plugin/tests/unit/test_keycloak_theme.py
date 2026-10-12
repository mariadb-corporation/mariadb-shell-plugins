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

"""The Keycloak login theme in ``keycloak-theme/``.

The theme gives a Keycloak realm the look of the built-in authorization
server's sign-in page by carrying copies of that page's stylesheet and seal.
Nothing builds the theme from them, so these checks are what keeps the copies
from drifting when the server's page changes.
"""

# cSpell:ignore mysqlsh MariaDB Keycloak keycloak noparse

import os

from mcp_plugin.lib import oauth_builtin

THEME = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "keycloak-theme",
    "mariadb-mcp",
    "login",
)


def _read(*path):
    with open(os.path.join(THEME, *path), encoding="utf-8") as file:
        return file.read()


def test_the_stylesheet_carries_the_sign_in_page_style():
    assert oauth_builtin._PAGE_STYLE in _read("resources", "css", "mariadb-mcp.css")


def test_the_seal_is_the_one_the_sign_in_page_shows():
    seal = _read("seal.ftl")

    # Inside noparse, so that FreeMarker never reads the SVG as a template.
    assert f"<#noparse>{oauth_builtin._seal_svg().strip()}</#noparse>" in seal


def test_the_theme_extends_the_base_theme_with_its_own_stylesheet():
    properties = dict(
        line.split("=", 1)
        for line in _read("theme.properties").splitlines()
        if line and not line.startswith("#")
    )

    assert properties["parent"] == "base"
    assert properties["styles"] == "css/mariadb-mcp.css"
    assert os.path.isfile(os.path.join(THEME, "resources", properties["styles"]))


def test_the_page_frame_is_the_only_template_layout():
    # Every page of the theme, its own and the base theme's, is laid out by
    # template.ftl, which therefore has to define the macro they all call.
    assert "<#macro registrationLayout " in _read("template.ftl")
    assert '<#import "template.ftl" as layout>' in _read("login.ftl")
