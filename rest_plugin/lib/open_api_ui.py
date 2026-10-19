# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""An OpenAPI UI for a REST service: Swagger UI, ready to be a content set.

The page a REST service's content set serves at, say, /openApiUi: Swagger
UI's `dist` files, patched so that they

* load the service's OpenAPI catalog, `<service path>/open-api-catalog/`,
  which the REST Daemon serves for a published service;
* send the Authorize button to the REST Daemon's sign-in page, which comes
  back to the UI once the user is signed in;
* are titled after the service, carry the MariaDB favicons, and have a
  dark theme.

The MySQL Shell for VS Code did the same for a content set whose folder was
the placeholder `${openApiUi}`; here it is an option of its own
(`load_content_set(..., open_api_ui=True)`).

Both downloads are pinned and checked against their SHA-256, and kept by
it in the plugin's data folder, so they are downloaded once. Each patch
must apply: it is written against the minified files of that one release,
and a page that silently stayed Swagger's own petstore demo would be worse
than an error.
"""

import hashlib
import html
import io
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SWAGGER_UI_VERSION = "5.17.14"
SWAGGER_UI_URL = (
    "https://github.com/swagger-api/swagger-ui/archive/refs/tags/"
    f"v{SWAGGER_UI_VERSION}.zip"
)
SWAGGER_UI_SHA256 = "481244d0812097b11fbaeef79f71d942b171617f9c9f9514e63acbe13e71ccdc"

DARK_CSS_URL = (
    "https://github.com/Amoenus/SwaggerDark/releases/download/v1.0.0/"
    "SwaggerDark.css"
)
DARK_CSS_SHA256 = "2f9907ffd85f914b54113beb700bfabac2e0cb24b234b7ad3e87edcaf31774f6"

DOWNLOAD_TIMEOUT = 120

# The default request path of an OpenAPI UI content set.
DEFAULT_REQUEST_PATH = "/openApiUi"

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# What the layout needs besides the dark theme: room above the page, and
# the Authorize button and the spec selector in the top bar.
_LAYOUT_CSS = """
#swagger-ui {
padding-top: 20px;
}

.swagger-ui .scheme-container {
position: absolute;
right: 0px;
top: 0px;
height: 58px;
padding: 10px 0px;
background: unset;
box-shadow: unset;
}

.swagger-ui .btn.authorize span {
padding: 4px 10px 0 0;
}

.swagger-ui .topbar {
background-color: unset;
padding: 10px 0;
position: absolute;
top: 0px;
right: 170px;
width: 300px;
font-size: 0.75em;
}

.swagger-ui .topbar .download-url-wrapper .download-url-button {
padding: 3px 10px 0px 10px;
font-size: 1.2em;
}

.swagger-ui .topbar .wrapper {
padding: 0;
}
"""


def cache_dir():
    """Where the downloads are kept, by their SHA-256."""
    import mysqlsh

    return os.path.abspath(
        mysqlsh.plugin_manager.general.get_shell_user_dir(
            "plugin_data", "rest_plugin", "downloads"
        )
    )


def _cached(sha256):
    """The download kept with this SHA-256, or None where it is not, or is
    not that file any more."""
    try:
        with open(os.path.join(cache_dir(), sha256), "rb") as file:
            data = file.read()
    except OSError:
        return None
    return data if hashlib.sha256(data).hexdigest() == sha256 else None


def _keep(sha256, data):
    """Keeps a download for the next time; a folder that cannot be written
    only means it is downloaded again."""
    directory = cache_dir()
    temporary = os.path.join(directory, f"{sha256}.tmp")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(temporary, "wb") as file:
            file.write(data)
        os.replace(temporary, os.path.join(directory, sha256))
    except OSError:
        pass


def download(url, sha256):
    """Downloads a file and checks it against its SHA-256, unless it was
    downloaded before.

    Args:
        url (str): The file.
        sha256 (str): Its SHA-256, in hex.

    Returns:
        bytes: Its content.

    Raises:
        ValueError: If it cannot be downloaded, or is not the file expected.
    """
    data = _cached(sha256)
    if data is None:
        data = _fetch(url, sha256)
        _keep(sha256, data)
    return data


def _fetch(url, sha256):
    """Downloads a file and checks it against its SHA-256."""
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
            data = response.read()
    except (urllib.error.URLError, OSError) as error:
        raise ValueError(
            f"Could not download {url}: {error}. The OpenAPI UI needs access "
            "to github.com."
        ) from error

    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(
            f"{url} is not the file expected: its SHA-256 does not match."
        )

    return data


def _dist_files(archive):
    """Swagger UI's own `dist` files, by name.

    Only the top-level `dist` folder - the archive has a `dist` per flavour
    too - and neither the source maps nor the ES module bundles, which the
    page does not load.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as zip_file:
        for info in zip_file.infolist():
            # Zip entry names use "/" whatever the platform.
            parts = info.filename.split("/")
            if info.is_dir() or len(parts) != 3 or parts[1] != "dist":
                continue
            name = parts[2]
            if name.startswith(".") or name.endswith(".map") or "-es-" in name:
                continue
            yield name, zip_file.read(info)


def _patch(path, pattern, replacement, flags=0):
    """Replaces what a pattern finds in a file, failing where it finds nothing."""
    with open(path, "r", encoding="utf-8") as file:
        content = file.read()

    patched, count = re.subn(pattern, lambda _match: replacement, content, flags=flags)
    if count == 0:
        raise ValueError(
            f"The OpenAPI UI's {os.path.basename(path)} could not be patched: "
            "it is not the file of Swagger UI "
            f"{SWAGGER_UI_VERSION}."
        )

    with open(path, "w", encoding="utf-8") as file:
        file.write(patched)


def download_assets(send_gui_message=None, fetch=None):
    """Swagger UI and the dark theme, downloaded unless they were before.

    What needs no database: a caller holding a transaction, or a
    connection's lock, calls it first, so that prepare() only reads what
    is kept.

    Args:
        send_gui_message (callable): Where to report a download.
        fetch (callable): Downloads a URL checked against a SHA-256; a test
            can hand in its own.

    Returns:
        tuple: The Swagger UI archive and the dark theme's CSS.
    """
    assets = []
    for url, sha256, what in (
        (SWAGGER_UI_URL, SWAGGER_UI_SHA256, f"Swagger UI {SWAGGER_UI_VERSION}"),
        (DARK_CSS_URL, DARK_CSS_SHA256, "the dark theme"),
    ):
        data = _cached(sha256) if fetch is None else None
        if data is None:
            if send_gui_message is not None:
                send_gui_message("info", f"Downloading {what} ...")
            data = (fetch or download)(url, sha256)
        assets.append(data)
    return tuple(assets)


def prepare(
    target_dir,
    service_name,
    service_path,
    request_path=DEFAULT_REQUEST_PATH,
    send_gui_message=None,
    fetch=None,
):
    """Writes a REST service's OpenAPI UI into a folder.

    Args:
        target_dir (str): The folder, which must exist and be empty.
        service_name (str): The service's name, for the page's title.
        service_path (str): The service's request path, `/myService`.
        request_path (str): The content set's request path the UI is served
            at, `/openApiUi`.
        send_gui_message (callable): Where to report progress.
        fetch (callable): Downloads a URL checked against a SHA-256; a test
            can hand in its own.

    Returns:
        list: The names of the files written.
    """

    def report(message):
        if send_gui_message is not None:
            send_gui_message("info", message)

    archive, dark_css = download_assets(send_gui_message, fetch)

    report("Customizing the OpenAPI UI ...")
    names = []
    for name, data in _dist_files(archive):
        with open(os.path.join(target_dir, name), "wb") as file:
            file.write(data)
        names.append(name)

    def path_of(name):
        return os.path.join(target_dir, name)

    # Whatever the service's name and paths hold, they stay what they are:
    # URL parts are percent-encoded, JavaScript strings written by
    # json.dumps, and the title HTML-escaped. The page is served to
    # everyone by default, so a name must not be able to add a script.
    def url_path(path):
        return urllib.parse.quote(path, safe="/")

    # The service's own catalog instead of the petstore demo.
    _patch(
        path_of("swagger-initializer.js"),
        r'url: ".*?"',
        "url: " + json.dumps(url_path(f"{service_path}/open-api-catalog/")),
    )

    # Signing in is the REST Daemon's: its sign-in page, which returns to
    # the UI afterwards, rather than Swagger's own dialog.
    redirect = (
        f"/?service={url_path(service_path)}"
        f"&redirectUrl={url_path(f'{service_path}{request_path}/index.html')}"
    )
    _patch(
        path_of("swagger-ui-bundle.js"),
        r'"btn authorize unlocked",onClick:i}',
        '"btn authorize unlocked",onClick:()=>{window.location.href='
        + json.dumps(redirect)
        + ";}}",
    )

    # No link to swagger.io in the top bar.
    _patch(
        path_of("swagger-ui.css"),
        r"\.swagger-ui \.topbar a\{align-items:center;color:#fff;display:flex;",
        ".swagger-ui .topbar a{align-items:center;color:#fff;display:none;",
    )

    _patch(
        path_of("index.html"),
        r"Swagger UI",
        f"{html.escape(service_name)} - OpenAPI UI",
    )
    # The MariaDB favicons instead of Swagger's.
    _patch(
        path_of("index.html"),
        r'[ \t]*<link rel="icon" type="image/png" href="\./favicon-32x32\.png" '
        r'sizes="32x32" />\n[ \t]*<link rel="icon" type="image/png" '
        r'href="\./favicon-16x16\.png" sizes="16x16" />',
        '    <link rel="icon" href="./favicon.ico" sizes="any" />\n'
        '    <link rel="icon" href="./favicon.svg" type="image/svg+xml" />',
    )
    for png in ("favicon-16x16.png", "favicon-32x32.png"):
        os.remove(path_of(png))
        names.remove(png)
    for icon in ("favicon.ico", "favicon.svg"):
        shutil.copyfile(os.path.join(_ASSETS, icon), path_of(icon))
        names.append(icon)

    with open(path_of("index.css"), "ab") as file:
        file.write(b"\n")
        file.write(dark_css)
        file.write(_LAYOUT_CSS.encode("utf-8"))

    return sorted(names)
