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

"""The one HTTP client every vendor plugin talks to its vendor's API through.

The vendors' own Python SDKs were deliberately not used: they need
``requests`` and ``python-dateutil``, which the shell does not bundle, and
MariaDB Cloud has no SDK at all. All three APIs are JSON over HTTPS, so one
small client on the bundled ``httpx2`` serves them alike, with the same
retries and the same error messages.

What it adds over a bare ``httpx2.Client``:

* Retries of what is safe to retry: a 429 always (the vendor did nothing),
  a 502, 503 or 504 and a failed connection only for an idempotent method.
  ``Retry-After`` is honoured, up to :data:`MAX_RETRY_DELAY`.
* Every failure becomes a :class:`VendorApiError` whose message is the
  vendor's own, found in whatever shape its error body has.
* The credentials never reach a message: errors name the method, the path
  and the vendor's answer, never a header.
"""

# cSpell:ignore mysqlsh MariaDB httpx

import time
from typing import Callable, Optional

from dba_plugin.lib.general import DbaError

# How often a request is retried, and the longest a retry waits.
MAX_RETRIES = 3
MAX_RETRY_DELAY = 30.0
DEFAULT_TIMEOUT = 30.0

_IDEMPOTENT_METHODS = ("GET", "HEAD", "PUT", "DELETE", "OPTIONS")
_RETRY_STATUSES = (502, 503, 504)

# Replaced by the tests, so that no request leaves the process.
_transport_factory: Optional[Callable] = None
_sleep = time.sleep


class VendorApiError(DbaError):
    """A request the vendor's API refused or that did not reach it.

    Attributes:
        status (int): The HTTP status, or None when there was no answer.
        code (str): The vendor's own error code, where it gives one.
    """

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def _error_details(body) -> tuple:
    """Finds the message and code in an error body, whatever its shape.

    Hetzner answers ``{"error": {"code", "message"}}``, UpCloud
    ``{"error": {"error_code", "error_message"}}``, MariaDB Cloud
    ``{"errors": [{"message", ...}]}`` or ``{"detail": ...}``; the rest is
    covered by the plain keys.

    Returns:
        tuple: The message (or None) and the code (or None).
    """
    if isinstance(body, list) and body:
        return _error_details(body[0])
    if not isinstance(body, dict):
        return None, None

    for key in ("error", "errors"):
        if key in body and isinstance(body[key], (dict, list)):
            message, code = _error_details(body[key])
            if message:
                return message, code

    message = None
    for key in ("message", "error_message", "detail", "title", "error"):
        value = body.get(key)
        if isinstance(value, str) and value:
            message = value
            break
        if isinstance(value, list) and value:
            # FastAPI-style validation errors: [{"loc": [...], "msg": "..."}].
            message = "; ".join(
                str(item.get("msg", item)) if isinstance(item, dict) else str(item)
                for item in value
            )
            break

    code = None
    for key in ("code", "error_code", "type"):
        if body.get(key) not in (None, ""):
            code = str(body[key])
            break

    return message, code


def _retry_delay(response, attempt: int) -> float:
    """How long to wait before the next attempt."""
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), MAX_RETRY_DELAY)
            except ValueError:
                pass

    return min(2.0**attempt, MAX_RETRY_DELAY)


class RestClient:
    """A JSON client for one vendor API.

    Attributes:
        last_headers: The headers of the last answer, for what a vendor
            sends there rather than in the body - MariaDB Cloud's next-page
            token. None before the first answer.

    Args:
        vendor (str): The vendor's display name, for messages.
        base_url (str): The API's base URL; paths are relative to it.
        headers (dict): Headers sent with every request, the credentials
            among them.
        auth (tuple): A (user, password) pair for HTTP basic authentication.
        timeout (float): Seconds a request may take.
    """

    def __init__(
        self,
        vendor: str,
        base_url: str,
        headers: Optional[dict] = None,
        auth: Optional[tuple] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        import httpx2

        self.vendor = vendor
        options = {
            "base_url": base_url,
            "headers": {"Accept": "application/json", **(headers or {})},
            "timeout": timeout,
        }
        if auth is not None:
            options["auth"] = auth
        if _transport_factory is not None:
            options["transport"] = _transport_factory()

        self._client = httpx2.Client(**options)
        self.last_headers = None

    def close(self) -> None:
        """Closes the client's connections."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def request(self, method: str, path: str, params=None, json=None):
        """Sends a request and returns its JSON answer.

        Args:
            method (str): The HTTP method.
            path (str): The path, relative to the base URL.
            params (dict): The query parameters; None values are left out.
            json: The request body, sent as JSON.

        Returns:
            The decoded JSON body, or None for an empty one.

        Raises:
            VendorApiError: The request failed, after the retries it allows.
        """
        import httpx2

        method = method.upper()
        if params:
            params = {key: value for key, value in params.items() if value is not None}

        attempt = 0
        while True:
            response = None
            try:
                response = self._client.request(method, path, params=params, json=json)
            except httpx2.TimeoutException as e:
                failure = f"timed out ({e.__class__.__name__})"
            except httpx2.TransportError as e:
                failure = f"could not be sent: {e}"
            else:
                failure = None

            retry = attempt < MAX_RETRIES and (
                (response is not None and response.status_code == 429)
                or (
                    method in _IDEMPOTENT_METHODS
                    and (response is None or response.status_code in _RETRY_STATUSES)
                )
            )
            if retry:
                _sleep(_retry_delay(response, attempt))
                attempt += 1
                continue

            if response is None:
                raise VendorApiError(
                    f"The {self.vendor} API request {method} {path} {failure}."
                )

            self.last_headers = response.headers
            return self._answer(method, path, response)

    def _answer(self, method, path, response):
        """The body of a response, or the error it reports."""
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = None

        if response.is_success:
            return body

        message, code = _error_details(body)
        if not message:
            message = (response.text or "").strip()[:500] or response.reason_phrase
        raise VendorApiError(
            f"The {self.vendor} API refused {method} {path} with HTTP "
            f"{response.status_code}"
            + (f" ({code})" if code else "")
            + f": {message}",
            status=response.status_code,
            code=code,
        )

    def get(self, path, params=None):
        """Sends a GET request. See :meth:`request`."""
        return self.request("GET", path, params=params)

    def post(self, path, json=None, params=None):
        """Sends a POST request. See :meth:`request`."""
        return self.request("POST", path, params=params, json=json)

    def delete(self, path, params=None, json=None):
        """Sends a DELETE request. See :meth:`request`."""
        return self.request("DELETE", path, params=params, json=json)
