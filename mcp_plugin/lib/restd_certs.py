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

"""The HTTPS certificate every REST Daemon on this machine serves.

One local certificate authority and one `localhost` server certificate, in
`<deployments root>/web-certs`, made by the first REST Daemon setup and
given to every one after it. Trusting the CA once - in the login keychain
on macOS, the user's Root store on Windows, the NSS database Chromium and
VS Code read on Linux - makes a browser trust every daemon's
`https://localhost:<port>`.

What mariadb-restd generates by itself would not do: its certificate names
the host in its common name only, which browsers ignore, and each
deployment would have a CA of its own to trust.

Trusting it is done as the MySQL Shell for VS Code's welcome wizard did,
and macOS and Windows ask the user to confirm in a system dialog. Unlike
the wizard, a dialog that is cancelled is reported as such: the command's
exit code is checked, and then whether the CA is trusted at all.
"""

# cSpell:ignore restd MariaDB keychain certutil nssdb SHA

import datetime
import ipaddress
import os
import shutil
import subprocess

from mcp_plugin.lib import restd
from mcp_plugin.lib.tool_registrar import tool_error

CERTS_DIR_NAME = "web-certs"
CA_FILE = "rootCA.crt"
SERVER_CERT_FILE = "server.crt"
SERVER_KEY_FILE = "server.key"

CA_NAME = "MariaDB REST Daemon Local CA"
# The nickname the CA has in an NSS database.
NSS_NICKNAME = "MariaDB REST Daemon"

# macOS refuses a certificate valid for longer than 825 days.
VALIDITY_DAYS = 824
# A certificate this close to its end is made anew rather than used.
RENEW_DAYS = 30

# How long a system dialog may wait for the user.
TRUST_TIMEOUT = 600


def certs_dir() -> str:
    """The folder of the certificate, beside the deployments of other servers."""
    return os.path.join(restd.deployments_root(), CERTS_DIR_NAME)


def _paths() -> dict:
    folder = certs_dir()
    return {
        "ca": os.path.join(folder, CA_FILE),
        "cert": os.path.join(folder, SERVER_CERT_FILE),
        "key": os.path.join(folder, SERVER_KEY_FILE),
    }


def _load(path):
    from cryptography import x509

    with open(path, "rb") as file:
        return x509.load_pem_x509_certificate(file.read())


def _usable(paths: dict) -> bool:
    """Whether the files are there and the certificates not about to end."""
    if not all(os.path.isfile(path) for path in paths.values()):
        return False
    try:
        limit = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=RENEW_DAYS
        )
        return all(
            _load(paths[name]).not_valid_after_utc > limit for name in ("ca", "cert")
        )
    except (OSError, ValueError):
        return False


def _create(paths: dict) -> None:
    """Makes the CA and the server certificate it signs.

    The CA's private key is not kept: nothing else is ever signed with it,
    so there is no key on disk that could sign a certificate for another
    host. A new CA is made when the certificates come near their end.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    not_before = now - datetime.timedelta(days=1)
    not_after = now + datetime.timedelta(days=VALIDITY_DAYS)

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MariaDB"),
        x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "MariaDB"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        # What browsers check the host against; the common name is not.
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("::1")),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    os.makedirs(os.path.dirname(paths["ca"]), exist_ok=True)
    pem = serialization.Encoding.PEM
    _write(paths["ca"], ca_cert.public_bytes(pem))
    _write(paths["cert"], server_cert.public_bytes(pem))
    _write(
        paths["key"],
        key.private_bytes(
            pem,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        private=True,
    )


def _write(path, data: bytes, private=False) -> None:
    """Writes a file whole; a private one readable by its owner alone."""
    temporary = path + ".tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(temporary, flags, 0o600 if private else 0o644), "wb") as file:
        file.write(data)
    os.replace(temporary, path)


def ensure() -> dict:
    """The certificate the REST Daemons serve, made where there is none yet.

    Returns:
        dict: The paths of the CA (`ca`), the server certificate (`cert`) and
        its key (`key`).
    """
    paths = _paths()
    if not _usable(paths):
        _create(paths)
    return paths


def ca_fingerprint() -> str:
    """The CA's SHA-1 fingerprint, as the stores list it: lower case hex."""
    from cryptography.hazmat.primitives import hashes

    return _load(_paths()["ca"]).fingerprint(hashes.SHA1()).hex()


# --------------------------------------------------------------------------- #
# The system's trust store
# --------------------------------------------------------------------------- #
def _run(command, timeout=60):
    """Runs a command with stdin of its own; returns (exit code, output)."""
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise tool_error(
            f"{command[0]} did not finish within {timeout} seconds; the "
            "system dialog was probably left open."
        ) from error
    except OSError as error:
        raise tool_error(f"{command[0]} could not be run: {error}") from error
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def _login_keychain() -> str:
    return os.path.join(
        os.path.expanduser("~"), "Library", "Keychains", "login.keychain-db"
    )


def _nss_db() -> str:
    return "sql:" + os.path.join(os.path.expanduser("~"), ".pki", "nssdb")


def _platform() -> str:
    if os.name == "nt":
        return "windows"
    import sys

    return "macos" if sys.platform == "darwin" else "linux"


def is_trusted() -> bool:
    """Whether the system trusts the CA, and so the daemons' certificate.

    Asked of the system itself, not of what is stored: on macOS a
    certificate can be in the keychain without being trusted - a cancelled
    authorization leaves it there so.
    """
    paths = _paths()
    if not os.path.isfile(paths["ca"]):
        return False
    platform = _platform()

    if platform == "macos":
        code, _ = _run([
            "security", "verify-cert", "-c", paths["cert"], "-p", "ssl",
            "-s", "localhost", "-q",
        ])
        return code == 0

    fingerprint = ca_fingerprint()
    if platform == "windows":
        code, output = _run(
            ["certutil.exe", "-verifystore", "-user", "ROOT", CA_NAME]
        )
        found = [
            line.split(":", 1)[1].strip().replace(" ", "").lower()
            for line in output.splitlines()
            if "(sha1):" in line.lower()
        ]
        return code == 0 and fingerprint in found

    if shutil.which("certutil") is None:
        return False
    code, output = _run(["certutil", "-d", _nss_db(), "-L", "-n", NSS_NICKNAME])
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if "Fingerprint (SHA1)" in line and index + 1 < len(lines):
            return lines[index + 1].strip().replace(":", "").lower() == fingerprint
    return False


def trust() -> dict:
    """Has the system trust the CA, unless it does already.

    macOS asks for the user's password or Touch ID, Windows asks to confirm
    installing a certificate authority. Neither needs administrator rights:
    the CA goes into the user's own login keychain and Root store. On Linux
    it goes into the user's NSS database, without a dialog.

    Returns:
        dict: `trusted` (True), `changed` (whether it was added now), and
        `ca_path`.

    Raises:
        ToolError: If the user refused, or the store could not be changed.
    """
    paths = ensure()
    report = {"trusted": True, "changed": False, "ca_path": paths["ca"]}
    if is_trusted():
        return report

    platform = _platform()
    if platform == "macos":
        command = [
            "security", "add-trusted-cert", "-r", "trustRoot",
            "-k", _login_keychain(), paths["ca"],
        ]
    elif platform == "windows":
        command = ["certutil.exe", "-addstore", "-user", "-f", "ROOT", paths["ca"]]
    else:
        if shutil.which("certutil") is None:
            raise tool_error(
                "certutil is not installed, so the certificate cannot be added "
                "to the NSS database browsers read. Install libnss3-tools "
                "(Debian, Ubuntu) or nss-tools (Fedora), or trust "
                f"{paths['ca']} by hand."
            )
        os.makedirs(_nss_db()[len("sql:"):], exist_ok=True)
        code, _ = _run(["certutil", "-d", _nss_db(), "-L"])
        if code != 0:
            _run(["certutil", "-d", _nss_db(), "-N", "--empty-password"])
        # An older copy of it is replaced, not kept beside the new one.
        _run(["certutil", "-d", _nss_db(), "-D", "-n", NSS_NICKNAME])
        command = [
            "certutil", "-d", _nss_db(), "-A", "-t", "C,,",
            "-n", NSS_NICKNAME, "-i", paths["ca"],
        ]

    code, output = _run(command, timeout=TRUST_TIMEOUT)
    if code != 0 or not is_trusted():
        raise tool_error(
            "The certificate was not trusted"
            + (" - the confirmation was cancelled" if platform != "linux" else "")
            + (f": {output}" if output else ".")
        )

    report["changed"] = True
    return report
