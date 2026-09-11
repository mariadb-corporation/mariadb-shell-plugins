#!/usr/bin/env python3
# Copyright (c) 2026, MariaDB plc.
#
# Bumps the plugin suite version across every file that has historically
# been touched by the "Updating version to X.Y.Z" commits (see VERSION,
# a286d58b, 1d6a9c37, 99b54290 for the pattern this codifies).
#
# Add or remove an entry in TARGETS below to change which files are
# updated -- each entry is a regex that must match exactly once in the
# file, plus a template for the replacement text ("{v}" is substituted
# with the new version string).

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "VERSION"
VERSION_PATTERN = re.compile(r"^VERSION = (\S+)$", re.MULTILINE)

# One entry per file that must change on a version bump.
TARGETS = [
    {
        "file": "VERSION",
        "pattern": re.compile(r"^VERSION = \S+$", re.MULTILINE),
        "replacement": "VERSION = {v}",
    },
    {
        "file": "mrs_plugin/docs/VERSION",
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
        "file": "mrs_plugin/docs/index.html",
        "pattern": re.compile(r"Manual \d+\.\d+\.\d+</h3>"),
        "replacement": "Manual {v}</h3>",
    },
    {
        "file": "mrs_plugin/docs/quickstart.html",
        "pattern": re.compile(r"Guide \d+\.\d+\.\d+</h3>"),
        "replacement": "Guide {v}</h3>",
    },
    {
        "file": "mrs_plugin/docs/restApi.html",
        "pattern": re.compile(r"APIs \d+\.\d+\.\d+</h3>"),
        "replacement": "APIs {v}</h3>",
    },
    {
        "file": "mrs_plugin/docs/sdk.html",
        "pattern": re.compile(r"Reference \d+\.\d+\.\d+</h3>"),
        "replacement": "Reference {v}</h3>",
    },
    {
        "file": "mrs_plugin/docs/sql.html",
        "pattern": re.compile(r"Reference \d+\.\d+\.\d+</h3>"),
        "replacement": "Reference {v}</h3>",
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
        return {"target": target, "ok": False, "reason": reason}

    old_text = target["pattern"].search(content).group(0)
    new_text = target["replacement"].format(v=new_version)
    new_content = target["pattern"].sub(lambda m: new_text, content, count=1)
    return {
        "target": target,
        "abs_path": abs_path,
        "ok": True,
        "old_text": old_text,
        "new_text": new_text,
        "new_content": new_content,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump the plugin suite version across all known version files.")
    parser.add_argument("version", nargs="?", help="New version, e.g. 26.9.2. Prompted for if omitted.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing files.")
    args = parser.parse_args()

    current_version = read_current_version()
    print(f"Current version: {current_version}")

    new_version = args.version
    if not new_version:
        new_version = input("New version: ").strip()

    if not new_version:
        print("No version entered, aborting.")
        return 1
    if not re.fullmatch(r"\d+\.\d+\.\d+", new_version):
        print(f'"{new_version}" doesn\'t look like X.Y.Z -- aborting.')
        return 1

    plans = [plan_update(target, new_version) for target in TARGETS]
    failed = [p for p in plans if not p["ok"]]
    if failed:
        print("Refusing to write anything -- some files didn't match as expected:")
        for p in failed:
            print(f"  {p['target']['file']}: {p['reason']}")
        return 1

    for p in plans:
        print(f"  {p['target']['file']}: {p['old_text']}  ->  {p['new_text']}")

    if args.dry_run:
        print("\nDry run, no files written.")
        return 0

    for p in plans:
        p["abs_path"].write_text(p["new_content"], encoding="utf-8")

    print(f"\nUpdated {len(plans)} files to version {new_version}.")
    print(f"Review with \"git diff\", then commit as: Updating version to {new_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
