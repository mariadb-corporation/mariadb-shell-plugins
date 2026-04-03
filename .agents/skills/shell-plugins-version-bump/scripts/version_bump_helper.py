#!/usr/bin/env python3

# Copyright (c) 2026, Oracle and/or its affiliates.
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
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


VERSION_RE = re.compile(r"^\d{4}\.\d+\.\d+\+\d+\.\d+\.\d+$")
CHANGELOG_HEADER_RE = re.compile(r"^## Changes in ([^\s]+)\s*$")
TOKEN_RE = re.compile(r"^(WL#\d+|BUG#\d+)\b[: -]*", re.IGNORECASE)

MAINTENANCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bmerge\b",
        r"\bversion bump\b",
        r"\bupgrade(?:d)? version\b",
        r"\bbump version\b",
        r"\bchangelog\b",
        r"\btest(?:s| suite)?\b",
        r"\be2e\b",
        r"\bunit test\b",
        r"\bci\b",
        r"\blicen[cs]e\b",
        r"\bformat(?:ting)?\b",
        r"\blint\b",
        r"\btypo\b",
        r"\bmaintenance\b",
    ]
]

COMMON_FILENAMES = {
    "general.py",
    "VERSION",
    "package.json",
    "README.md",
}
MAX_COMMIT_LINE_LENGTH = 72


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def changelog_path() -> Path:
    return repo_root() / "gui" / "extension" / "CHANGELOG.md"


def parse_versions(path: Path) -> Tuple[str, str]:
    versions = []  # type: List[str]
    for line in path.read_text(encoding="utf-8").splitlines():
        match = CHANGELOG_HEADER_RE.match(line)
        if match:
            versions.append(match.group(1))
        if len(versions) == 2:
            break

    if len(versions) < 2:
        raise ValueError(
            f"Expected at least two changelog version headings in {path}"
        )

    return versions[0], versions[1]


def extension_version(full_version: str) -> str:
    return full_version.split("+", 1)[0]


def shell_version(full_version: str) -> str:
    return full_version.split("+", 1)[1]


def validate_target(target: str) -> None:
    if not VERSION_RE.match(target):
        raise ValueError(
            "Target version must match YYYY.M.P+X.Y.Z, "
            f"got: {target!r}"
        )


def is_docs_html(path: Path) -> bool:
    return path.suffix == ".html" and any(parent.name == "docs" for parent in path.parents)


def matches_layout(path: Path) -> bool:
    if path.name in COMMON_FILENAMES:
        return True
    return is_docs_html(path)


def iter_candidate_files() -> Iterable[Path]:
    root = repo_root()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        if "__pycache__" in path.parts:
            continue
        if matches_layout(path):
            yield path


def replacement_mode(path: Path, current_full: str, previous_full: str,
                     current_ext: str, previous_ext: str) -> Optional[Dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    if current_full in text:
        return {"mode": "full", "replace": current_full, "source": "current"}
    if previous_full in text:
        return {"mode": "full", "replace": previous_full, "source": "previous"}
    if current_ext in text:
        return {"mode": "extension", "replace": current_ext, "source": "current"}
    if previous_ext in text:
        return {"mode": "extension", "replace": previous_ext, "source": "previous"}
    return None


def discover_files(target: str) -> List[Dict[str, str]]:
    current_full, previous_full = parse_versions(changelog_path())
    current_ext = extension_version(current_full)
    previous_ext = extension_version(previous_full)
    target_ext = extension_version(target)
    matches = []  # type: List[Dict[str, str]]

    for path in sorted(iter_candidate_files()):
        match = replacement_mode(
            path, current_full, previous_full, current_ext, previous_ext)
        if not match:
            continue
        matches.append(
            {
                "path": path.relative_to(repo_root()).as_posix(),
                "mode": match["mode"],
                "matched_version": match["source"],
                "replace": match["replace"],
                "with": target if match["mode"] == "full" else target_ext,
            }
        )

    return matches


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root(),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return result.stdout


def changelog_relpath() -> str:
    return changelog_path().relative_to(repo_root()).as_posix()


def find_changelog_heading_commit(version: str) -> str:
    heading = f"## Changes in {version}"
    history = git(
        "log",
        "--reverse",
        "--format=%H",
        "-S",
        heading,
        "--",
        changelog_relpath(),
    )

    for commit_hash in history.splitlines():
        patch = git(
            "show",
            "--format=",
            "--unified=0",
            commit_hash,
            "--",
            changelog_relpath(),
        )
        if f"+{heading}" in patch:
            return commit_hash

    raise ValueError(
        "Could not find the commit that introduced changelog heading "
        f"{heading!r}"
    )


def is_maintenance_commit(subject: str, body: str) -> bool:
    text = f"{subject}\n{body}".strip()
    return any(pattern.search(text) for pattern in MAINTENANCE_PATTERNS)


def clean_summary(subject: str) -> Tuple[Optional[str], str]:
    token_match = TOKEN_RE.match(subject)
    token = None
    summary = subject.strip()
    if token_match:
        token = token_match.group(1).upper()
        summary = summary[token_match.end():].strip()
    summary = summary.rstrip(".")
    summary = re.sub(r"\s+", " ", summary)
    return token, summary


def gather_commits() -> List[Dict[str, object]]:
    _, previous = parse_versions(changelog_path())
    # In upgrade mode, the top changelog entry is the stub for the upcoming
    # release. The second entry is the release being finalized, so collect
    # commits after the commit that introduced that previous heading.
    previous_heading_commit = find_changelog_heading_commit(previous)
    raw = git(
        "log",
        "--no-merges",
        "--format=%H%x1f%s%x1f%b%x1e",
        f"{previous_heading_commit}..HEAD",
    )

    commits = []  # type: List[Dict[str, object]]
    for record in raw.strip("\x1e").split("\x1e"):
        record = record.strip()
        if not record:
            continue
        commit_hash, subject, body = record.split("\x1f", 2)
        body = body.strip()
        maintenance = is_maintenance_commit(subject, body)
        token, summary = clean_summary(subject)
        section = "Additions" if subject.upper().startswith("WL#") else "Fixes"
        commits.append(
            {
                "hash": commit_hash,
                "subject": subject,
                "body": body,
                "token": token,
                "summary_hint": summary,
                "section": section,
                "maintenance": maintenance,
            }
        )
    return commits


def head_commit_has_literal_newlines() -> bool:
    message = git("log", "-1", "--format=%B")
    start = 0
    while True:
        index = message.find(r"\n", start)
        if index == -1:
            return False

        before = message[index - 1] if index > 0 else ""
        after_index = index + 2
        after = message[after_index] if after_index < len(message) else ""

        if before in {"'", '"'} and after == before:
            start = index + 2
            continue

        # Treat escaped newlines embedded in text as malformed, e.g.
        # "subject\n\nbody" or "foo\nbar".
        if before and after and not before.isspace() and not after.isspace():
            return True

        # A dangling token at the very start or end is also suspicious.
        if not before or not after:
            return True

        start = index + 2


def head_commit_overlong_lines() -> List[Dict[str, object]]:
    message = git("log", "-1", "--format=%B")
    overlong = []  # type: List[Dict[str, object]]

    for line_number, line in enumerate(message.splitlines(), 1):
        if len(line) > MAX_COMMIT_LINE_LENGTH:
            overlong.append(
                {
                    "line": line_number,
                    "length": len(line),
                    "text": line,
                }
            )

    return overlong


def cmd_versions(_: argparse.Namespace) -> int:
    current, previous = parse_versions(changelog_path())
    print(json.dumps(
        {
            "current": current,
            "previous": previous,
            "current_extension": extension_version(current),
            "current_shell": shell_version(current),
            "previous_extension": extension_version(previous),
            "previous_shell": shell_version(previous),
        },
        indent=2,
    ))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    validate_target(args.target)
    result = {
        "target": args.target,
        "files": discover_files(args.target),
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_commits(_: argparse.Namespace) -> int:
    current, previous = parse_versions(changelog_path())
    result = {
        "current": current,
        "previous": previous,
        "commits": gather_commits(),
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_check_head_commit(_: argparse.Namespace) -> int:
    has_literal_newlines = head_commit_has_literal_newlines()
    overlong_lines = head_commit_overlong_lines()
    print(
        json.dumps(
            {
                "literal_backslash_n": has_literal_newlines,
                "max_line_length": MAX_COMMIT_LINE_LENGTH,
                "overlong_lines": overlong_lines,
            },
            indent=2,
        )
    )
    return 0 if not has_literal_newlines and not overlong_lines else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Helper utilities for the shell-plugins version bump skill."
    )
    subparsers = parser.add_subparsers(dest="command")

    versions = subparsers.add_parser(
        "versions", help="Read current and previous versions from the changelog."
    )
    versions.set_defaults(func=cmd_versions)

    discover = subparsers.add_parser(
        "discover",
        help="Discover versioned files from repository patterns and content matches.",
    )
    discover.add_argument("--target", required=True, help="Target version.")
    discover.set_defaults(func=cmd_discover)

    commits = subparsers.add_parser(
        "commits",
        help="Gather non-merge commits since the previous changelog version.",
    )
    commits.set_defaults(func=cmd_commits)

    check = subparsers.add_parser(
        "check-head-commit",
        help=(
            "Check whether the latest commit message contains literal \\n "
            "sequences or lines longer than 72 characters."
        ),
    )
    check.set_defaults(func=cmd_check_head_commit)

    return parser


def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.func(args)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr)
        return exc.returncode
    except Exception as exc:  # pragma: no cover - CLI surface
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
