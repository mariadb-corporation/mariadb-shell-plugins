#!/usr/bin/env python3
# Copyright (c) 2026, MariaDB plc.
#
# Bumps every version this repo pins, in one go, as part of preparing a
# release -- that is, BEFORE the shell release is published:
#
# 1. The plugin suite version, across every file that has historically been
#    touched by the "Updating version to X.Y.Z" commits (see VERSION,
#    a286d58b, 1d6a9c37, 99b54290 for the pattern this codifies). It is
#    always the shell version from 2.: the plugins are released with it.
# 2. The MariaDB Shell version the plugins are released with -- read from
#    MYSQL_VERSION on the shell repo's main branch, where the shell version is
#    bumped for the release.
# 3. The sandbox server index (mcp_plugin/lib/sandbox_server_versions.json),
#    pointing each package at the release it is, or will be, published in.
#
# The release does not exist yet, so the index is built from what it will be
# made of. publish-release.yml attaches, for each of its server_11_tag,
# server_12_tag and server_13_tag inputs, the artifacts of the latest
# successful Sandbox Server run (sandbox-server.yml) whose run-name is
# "Sandbox Server <tag>". This script picks the tags -- the newest
# mariadb-<major>.x.y tag of MariaDB/server for each series -- finds those
# same runs the same way, downloads their artifacts (~10 MB each) and hashes
# the tarballs inside.
#
# A package whose checksum is the one already in the index, and which the
# release that index entry points at really carries under that checksum (its
# SERVER_SHA256SUMS says so), is left as it is: the same bytes do not need
# publishing again, and the entry keeps pointing at the release that already
# has them. Only a package that is new or changed gets a URL under the tag
# publish-release will give this release, v<MYSQL_VERSION>, and only a series
# with such a package has to be given to publish-release at all.
#
# The index is only right if the release is published from those same runs:
# a newer Sandbox Server run for one of the tags, finished between this script
# and the publish, is what publish-release would pick instead. The script
# prints the inputs to publish with, and when the release already exists it
# checks the index against the release's own SERVER_SHA256SUMS.
#
# Add or remove an entry in PLUGIN_TARGETS / SHELL_TARGETS below to change
# which files are updated -- each entry is a regex that must match exactly once
# in the file, plus a template for the replacement text ("{v}" is substituted
# with the new version string).
#
# Needs git and the GitHub CLI (gh), logged in with read access to the shell
# repo.

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "VERSION"
VERSION_PATTERN = re.compile(r"^VERSION = (\S+)$", re.MULTILINE)

SHELL_REPO = "mariadb-corporation/mariadb-shell"
SHELL_VERSION_BRANCH = "main"
SERVER_SOURCE_REPO = "https://github.com/MariaDB/server"
SANDBOX_SERVER_WORKFLOW = "sandbox-server.yml"

SERVER_INDEX_FILE = "mcp_plugin/lib/sandbox_server_versions.json"
SERVER_CHECKSUMS_ASSET = "SERVER_SHA256SUMS"
SUPPORTED_INDEX_VERSION = 1

# The server series publish-release ships, one per server_<major>_tag input.
# Every one of them must resolve to a complete build: a missing series would
# silently take that line out of the sandbox dialog.
SERVER_MAJORS = (11, 12, 13)

# How many recent successful Sandbox Server runs publish-release searches
# (`gh run list --limit 50` in its "Resolve Sandbox Server Runs" step). Kept
# the same so a run too old for it to find is not used here either.
RUN_SEARCH_LIMIT = 50

# A MariaDB/server release tag, e.g. "mariadb-11.8.9". Anything with a suffix
# (release candidates, "-1" re-tags) is not a release to ship.
SERVER_TAG_PATTERN = re.compile(r"^mariadb-(\d+)\.(\d+)\.(\d+)$")

# The platform keys the index uses (Node.js naming, see platform_key() in
# mcp_plugin/lib/sandbox_servers.py), one package per key per version.
EXPECTED_PLATFORMS = (
    "darwin-arm64",
    "darwin-x64",
    "linux-arm64",
    "linux-x64",
    "win32-arm64",
    "win32-x64",
)

# Every package name sandbox-server.yml produces, e.g.
# "mariadb-11.8.9-macos26-arm-64bit-sandbox.tar.gz" or
# "mariadb-11.8.9-linux-glibc2.34-x86-64bit-sandbox.tar.gz".
SERVER_PACKAGE_PATTERN = re.compile(
    r"^mariadb-(\d+)\.(\d+)\.(\d+)-(macos\d+|windows|linux-glibc[\d.]+)"
    r"-(arm|x86)-64bit-sandbox\.tar\.gz$"
)

# One entry per file that must change on a plugin version bump.
PLUGIN_TARGETS = [
    {
        "file": "VERSION",
        "pattern": re.compile(r"^VERSION = \S+$", re.MULTILINE),
        "replacement": "VERSION = {v}",
    },
    {
        "file": "mcp_plugin/lib/general.py",
        "pattern": re.compile(r'^VERSION = "[^"]+"$', re.MULTILINE),
        "replacement": 'VERSION = "{v}"',
    },
    {
        "file": "mcp_plugin/package.json",
        "pattern": re.compile(r'"version": "[^"]+"'),
        "replacement": '"version": "{v}"',
    },
    {
        "file": "mrs_plugin/lib/general.py",
        "pattern": re.compile(r'^VERSION = "[^"]+"$', re.MULTILINE),
        "replacement": 'VERSION = "{v}"',
    },
    {
        "file": "msm_plugin/lib/general.py",
        "pattern": re.compile(r'^VERSION = "[^"]+"$', re.MULTILINE),
        "replacement": 'VERSION = "{v}"',
    },
]

# One entry per file that names the shell release the plugins go out with.
# Not mcp_plugin/README.md's "ships with MariaDB Shell X and later" line: that
# is the first shell that bundled the plugin, a fact that does not move.
SHELL_TARGETS = [
    {
        # The shell the extension requires, and installs when there is none.
        "file": "code_ext/src/shell/constants.ts",
        "pattern": re.compile(r'^export const MINIMUM_SHELL_VERSION = "[^"]+";$', re.MULTILINE),
        "replacement": 'export const MINIMUM_SHELL_VERSION = "{v}";',
    },
]


def read_current_version() -> str:
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(content)
    if not match:
        raise SystemExit(f'Could not find "VERSION = ..." in {VERSION_FILE}')
    return match.group(1)


def plan_update(target: dict, new_version: str):
    abs_path = REPO_ROOT / target["file"]
    content = abs_path.read_text(encoding="utf-8")
    matches = target["pattern"].findall(content)
    count = len(matches)
    if count != 1:
        reason = "pattern not found" if count == 0 else f"pattern matched {count} times, expected exactly 1"
        return {"file": target["file"], "ok": False, "reason": reason}

    old_text = target["pattern"].search(content).group(0)
    new_text = target["replacement"].format(v=new_version)
    new_content = target["pattern"].sub(lambda m: new_text, content, count=1)
    return {
        "file": target["file"],
        "abs_path": abs_path,
        "ok": True,
        "old_text": old_text,
        "new_text": new_text,
        "new_content": new_content,
    }


def run(command: list, **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True, **kwargs)
    except FileNotFoundError:
        raise SystemExit(f"'{command[0]}' is needed but was not found. For gh, install it and run 'gh auth login'.")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        raise SystemExit(f"{' '.join(command)} failed:\n{stderr.strip()}")


def gh(*args: str) -> str:
    return run(["gh", *args], text=True).stdout


def gh_json(*args: str):
    return json.loads(gh(*args))


def read_shell_version(branch: str) -> str:
    """Returns the shell version on a branch, as publish-release derives it."""
    raw = gh(
        "api",
        f"repos/{SHELL_REPO}/contents/MYSQL_VERSION?ref={branch}",
        "-H",
        "Accept: application/vnd.github.raw",
    )
    fields = {}
    for key in ("MAJOR", "MINOR", "PATCH", "EXTRA"):
        match = re.search(rf"^MYSQL_VERSION_{key}=(.*)$", raw, re.MULTILINE)
        fields[key] = match.group(1).strip() if match else ""
    if not (fields["MAJOR"] and fields["MINOR"] and fields["PATCH"]):
        raise SystemExit(f"MYSQL_VERSION on {SHELL_REPO}@{branch} is missing MAJOR/MINOR/PATCH -- got:\n{raw}")
    return f"{fields['MAJOR']}.{fields['MINOR']}.{fields['PATCH']}{fields['EXTRA']}"


def latest_server_tags() -> dict:
    """Returns the newest MariaDB/server release tag of each series, by major."""
    output = run(
        ["git", "ls-remote", "--tags", "--refs", SERVER_SOURCE_REPO, "mariadb-*"], text=True
    ).stdout
    latest = {}
    for line in output.splitlines():
        tag = line.rpartition("refs/tags/")[2]
        match = SERVER_TAG_PATTERN.match(tag)
        if match is None:
            continue
        version = tuple(int(part) for part in match.groups())
        if version[0] in SERVER_MAJORS and version > latest.get(version[0], ()):
            latest[version[0]] = version
    return {major: "mariadb-{}.{}.{}".format(*version) for major, version in latest.items()}


def platform_key(platform_token: str, arch_token: str) -> str:
    if platform_token.startswith("macos"):
        system = "darwin"
    elif platform_token == "windows":
        system = "win32"
    else:
        system = "linux"
    return f"{system}-{'arm64' if arch_token == 'arm' else 'x64'}"


def find_publishable_run(tag: str, successful_runs: list):
    """Returns the run publish-release would attach for a tag, or None.

    Its lookup exactly: the newest successful run whose display title is
    "Sandbox Server <tag>", on any branch, among the most recent ones.
    """
    for candidate in successful_runs:
        if candidate["displayTitle"] == f"Sandbox Server {tag}":
            return candidate
    return None


def hash_run_packages(run_info: dict, problems: list) -> tuple:
    """Downloads a run's artifacts and hashes the sandbox tarballs inside.

    Returns:
        A tuple of ({package name: sha256}, earliest artifact expiry).
    """
    artifacts = gh_json(
        "api", f"repos/{SHELL_REPO}/actions/runs/{run_info['databaseId']}/artifacts?per_page=100"
    )["artifacts"]

    expired = [artifact["name"] for artifact in artifacts if artifact["expired"]]
    if expired:
        problems.append(
            f"run {run_info['databaseId']} ({run_info['displayTitle']}): artifacts expired "
            f"({', '.join(expired)}) -- rebuild it, publish-release could not attach them either"
        )
        return {}, None

    checksums = {}
    with tempfile.TemporaryDirectory(prefix="sandbox-server-") as work_dir:
        for artifact in artifacts:
            archive = Path(work_dir) / f"{artifact['id']}.zip"
            print(f"    downloading {artifact['name']} ({artifact['size_in_bytes'] // (1024 * 1024)} MB)")
            with archive.open("wb") as out:
                try:
                    subprocess.run(
                        ["gh", "api", f"repos/{SHELL_REPO}/actions/artifacts/{artifact['id']}/zip"],
                        check=True, stdout=out, stderr=subprocess.PIPE,
                    )
                except subprocess.CalledProcessError as exc:
                    raise SystemExit(f"Downloading artifact {artifact['name']} failed:\n{exc.stderr.decode().strip()}")

            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.namelist():
                    name = Path(member).name
                    if not name.endswith("-sandbox.tar.gz"):
                        continue
                    digest = hashlib.sha256()
                    with bundle.open(member) as package:
                        for chunk in iter(lambda: package.read(1024 * 1024), b""):
                            digest.update(chunk)
                    checksums[name] = digest.hexdigest()
            archive.unlink()

    return checksums, min(artifact["expires_at"] for artifact in artifacts)


def package_url(release_tag: str, name: str) -> str:
    return f"https://github.com/{SHELL_REPO}/releases/download/{release_tag}/{name}"


def split_package_url(url: str) -> tuple:
    """Returns (release tag, package name) from an index entry's URL."""
    tag, _, name = url.partition("/releases/download/")[2].partition("/")
    return tag, name


def index_packages(index: dict) -> dict:
    """Flattens an index into {(server version, platform key): package}."""
    return {
        (f"{entry['major']}.{entry['minor']}.{patch}", package["os"]): package
        for entry in index.get("serverVersions", [])
        for patch_group in entry.get("patches", [])
        for patch, packages in patch_group.items()
        for package in packages
    }


_published_checksums_cache = {}


def published_checksums(release_tag: str):
    """Returns {package name: sha256} from a release's SERVER_SHA256SUMS.

    None when the release is not published, or carries no such asset. Each
    release is fetched once per run.
    """
    if release_tag not in _published_checksums_cache:
        try:
            published = run(
                ["gh", "release", "download", release_tag, "-R", SHELL_REPO, "-p", SERVER_CHECKSUMS_ASSET, "-O", "-"],
                text=True,
            ).stdout
        except SystemExit:
            _published_checksums_cache[release_tag] = None
        else:
            checksums = {}
            for line in published.splitlines():
                sha256sum, _, name = line.strip().partition("  ")
                if name:
                    checksums[name.lstrip("*")] = sha256sum
            _published_checksums_cache[release_tag] = checksums
    return _published_checksums_cache[release_tag]


def collect_server_packages(release_tag: str, previous: dict):
    """Resolves each series' build the way publish-release will, and hashes it.

    A package whose checksum is what the previous index already has for that
    server version and platform, and which the release that entry points at
    really carries under that checksum, keeps its entry: the same bytes are
    out already and are not published again. Any other package is new, and
    goes under release_tag.

    Args:
        release_tag: The tag publish-release will give the shell release.
        previous: The current index, flattened by index_packages().

    Returns:
        A tuple of (the new index, {major: tag or None} to publish with -- a
        tag only for a series with a new package, None when the release has
        nothing to carry for it -- and the earliest artifact expiry among the
        runs to publish, or None when there is nothing to publish). Exits
        without returning when any series cannot be resolved to a complete
        build.
    """
    print(f"Finding the newest MariaDB/server tag of the {', '.join(f'{m}.x' for m in SERVER_MAJORS)} series...")
    tags = latest_server_tags()

    successful_runs = gh_json(
        "run", "list", "-R", SHELL_REPO, "--workflow", SANDBOX_SERVER_WORKFLOW,
        "--status", "success", "--limit", str(RUN_SEARCH_LIMIT),
        "--json", "databaseId,displayTitle,headBranch,createdAt",
    )
    pending_runs = [
        candidate
        for status in ("in_progress", "queued")
        for candidate in gh_json(
            "run", "list", "-R", SHELL_REPO, "--workflow", SANDBOX_SERVER_WORKFLOW,
            "--status", status, "--json", "databaseId,displayTitle",
        )
    ]

    problems = []
    series = {}
    to_publish = {major: None for major in SERVER_MAJORS}
    expiries = []
    for major in SERVER_MAJORS:
        tag = tags.get(major)
        if tag is None:
            problems.append(f"MariaDB/server has no mariadb-{major}.x.y release tag")
            continue

        build_hint = f"gh workflow run {SANDBOX_SERVER_WORKFLOW} -R {SHELL_REPO} -f mariadb_version={tag}"
        pending = [p for p in pending_runs if p["displayTitle"] == f"Sandbox Server {tag}"]
        if pending:
            problems.append(
                f"{tag}: Sandbox Server run {pending[0]['databaseId']} is still running -- publish-release "
                "would pick it once it succeeds, so wait for it and run this again"
            )
            continue

        run_info = find_publishable_run(tag, successful_runs)
        if run_info is None:
            problems.append(f"{tag}: no successful Sandbox Server build -- start one with: {build_hint}")
            continue

        print(f"  {tag}: run {run_info['databaseId']} ({run_info['headBranch']}, {run_info['createdAt']})")
        checksums, expiry = hash_run_packages(run_info, problems)

        packages = {}
        kept = {}  # platform key -> the release its unchanged package stays in
        for name, sha256sum in sorted(checksums.items()):
            match = SERVER_PACKAGE_PATTERN.match(name)
            if match is None:
                problems.append(f"{tag}: {name} does not look like a sandbox server package")
                continue
            version = "{}.{}.{}".format(*match.groups()[:3])
            if f"mariadb-{version}" != tag:
                problems.append(f"{tag}: run {run_info['databaseId']} built {name}, not {tag}")
                continue
            key = platform_key(*match.groups()[3:])

            previous_package = previous.get((version, key))
            if previous_package is not None and previous_package.get("sha256sum") == sha256sum:
                previous_tag, previous_name = split_package_url(previous_package.get("url", ""))
                if (published_checksums(previous_tag) or {}).get(previous_name) == sha256sum:
                    packages[key] = dict(previous_package)
                    kept[key] = previous_tag
                    continue
            packages[key] = {
                "os": key,
                "url": package_url(release_tag, name),
                "sha256sum": sha256sum,
            }

        missing = [key for key in EXPECTED_PLATFORMS if key not in packages]
        if checksums and missing:
            problems.append(f"{tag}: run {run_info['databaseId']} has no package for {', '.join(missing)}")
        if packages and not missing:
            series[tuple(int(part) for part in tag.split("-")[1].split("."))] = packages
            new = [key for key in sorted(packages) if key not in kept]
            if kept:
                print(f"    unchanged, kept in {', '.join(sorted(set(kept.values())))}: {', '.join(sorted(kept))}")
            if new:
                print(f"    new or changed, to publish in {release_tag}: {', '.join(new)}")
                to_publish[major] = tag
                if expiry:
                    expiries.append(expiry)

    if problems:
        raise SystemExit(
            "Refusing to write anything -- the sandbox servers for this release are not ready:\n"
            + "\n".join(f"  {p}" for p in problems)
        )

    index = {
        "sandboxServerIndexVersion": SUPPORTED_INDEX_VERSION,
        "serverVersions": [
            {
                "major": major,
                "minor": minor,
                "latestPatch": patch,
                "patches": [{str(patch): [packages[key] for key in sorted(packages)]}],
            }
            for (major, minor, patch), packages in sorted(series.items())
        ],
    }
    return index, to_publish, min(expiries) if expiries else None


def check_against_release(release_tag: str, index: dict) -> None:
    """Compares what the index expects of the release with its SERVER_SHA256SUMS, if it is out.

    Only the packages the index points at this release are checked: the ones
    kept in older releases were checked against those when they were kept.
    """
    ours = {}
    for package in index_packages(index).values():
        tag, name = split_package_url(package["url"])
        if tag == release_tag:
            ours[name] = package["sha256sum"]
    if not ours:
        return

    expected = published_checksums(release_tag)
    if expected is None:
        print(f"Shell release {release_tag} is not published yet; publish it from the runs above.")
        return

    differing = sorted(name for name in ours if ours[name] != expected.get(name))
    if differing:
        raise SystemExit(
            f"Refusing to write anything -- shell release {release_tag} is already published, and its "
            f"{SERVER_CHECKSUMS_ASSET} disagrees with the Sandbox Server runs for:\n"
            + "\n".join(f"  {name}" for name in differing)
            + "\nThe release was published from other runs, or a tag was rebuilt since."
        )
    print(f"Shell release {release_tag} is already published, and its {SERVER_CHECKSUMS_ASSET} matches.")


def plan_server_index(release_tag: str):
    abs_path = REPO_ROOT / SERVER_INDEX_FILE
    old_index = json.loads(abs_path.read_text(encoding="utf-8"))
    new_index, server_tags, expiry = collect_server_packages(release_tag, index_packages(old_index))
    check_against_release(release_tag, new_index)

    def summary(index):
        versions = []
        for entry in index.get("serverVersions", []):
            for patch_group in entry.get("patches", []):
                for patch in patch_group:
                    versions.append(f"{entry['major']}.{entry['minor']}.{patch}")
        tags = sorted({
            package["url"].split("/releases/download/")[1].split("/")[0]
            for entry in index.get("serverVersions", [])
            for patch_group in entry.get("patches", [])
            for packages in patch_group.values()
            for package in packages
        })
        return f"{', '.join(versions) or 'none'} (from {', '.join(tags) or 'nowhere'})"

    plan = {
        "file": SERVER_INDEX_FILE,
        "abs_path": abs_path,
        "ok": True,
        "old_text": summary(old_index),
        "new_text": summary(new_index),
        "new_content": json.dumps(new_index, indent=4) + "\n",
    }
    return plan, server_tags, expiry


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump the plugin suite version, the MariaDB Shell version it is released with, "
        "and the sandbox server index for that shell release."
    )
    parser.add_argument(
        "--shell-branch", default=SHELL_VERSION_BRANCH,
        help=f"Shell repo branch to read MYSQL_VERSION from (default: {SHELL_VERSION_BRANCH}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing files.")
    args = parser.parse_args()

    current_version = read_current_version()
    print(f"Current version: {current_version}")

    print(f"Reading the shell version from {SHELL_REPO}@{args.shell_branch}...")
    shell_version = read_shell_version(args.shell_branch)
    release_tag = f"v{shell_version}"
    print(f"Shell version: {shell_version} (release {release_tag})")

    # The plugins are released with the shell, under the same version.
    new_version = shell_version
    if not re.fullmatch(r"\d+\.\d+\.\d+", new_version):
        print(f'Shell version "{new_version}" doesn\'t look like X.Y.Z, so it cannot be the plugin version -- aborting.')
        return 1
    print(f"New plugin version: {new_version}")

    plans = [plan_update(target, new_version) for target in PLUGIN_TARGETS]
    plans += [plan_update(target, shell_version) for target in SHELL_TARGETS]
    failed = [p for p in plans if not p["ok"]]
    if failed:
        print("Refusing to write anything -- some files didn't match as expected:")
        for p in failed:
            print(f"  {p['file']}: {p['reason']}")
        return 1

    index_plan, server_tags, expiry = plan_server_index(release_tag)
    plans.append(index_plan)

    print()
    for p in plans:
        print(f"  {p['file']}: {p['old_text']}  ->  {p['new_text']}")

    if any(server_tags.values()):
        print("\nPublish the shell release with these publish-release.yml inputs, so it carries the new packages:")
        for major, tag in server_tags.items():
            print(f"  server_{major}_tag = {tag or '(blank: its packages are unchanged and already published)'}")
        print(f"before {expiry}, when the first of their build artifacts expires.")
    else:
        print(f"\nNo server package needs publishing with {release_tag}: every one is unchanged and already "
              "published. Leave the server_*_tag inputs of publish-release.yml blank.")

    if args.dry_run:
        print("\nDry run, no files written.")
        return 0

    written = 0
    for p in plans:
        if p["abs_path"].read_text(encoding="utf-8") != p["new_content"]:
            p["abs_path"].write_text(p["new_content"], encoding="utf-8")
            written += 1

    print(f"\nUpdated {written} files: plugins {new_version}, shell {shell_version}, "
          f"sandbox servers for {release_tag}.")
    print(f"Review with \"git diff\", then commit as: Updating version to {new_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
