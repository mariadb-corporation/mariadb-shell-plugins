---
name: shell-plugins-version-bump
description: Replace or upgrade the shell-plugins repository version using gui/extension/CHANGELOG.md as the source of truth. Use when a task asks to bump, replace, or upgrade the repo version to a target like YYYY.M.P+X.Y.Z, and the workflow must update the changelog, discover versioned files by repository pattern, commit in the required order, and verify commit messages do not contain literal \n sequences or overlong lines.
---

<!-- Copyright (c) 2026, Oracle and/or its affiliates.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License, version 2.0,
as published by the Free Software Foundation.

This program is designed to work with certain software (including
but not limited to OpenSSL) that is licensed under separate terms, as
designated in a particular file or component or in included license
documentation.  The authors of MySQL hereby grant you an additional
permission to link the program and your derivative works with the
separately licensed software that they have either included with
the program or referenced in the documentation.

This program is distributed in the hope that it will be useful,  but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
the GNU General Public License, version 2.0, for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA -->

# Shell Plugins Version Bump

Use this skill for repository version changes driven by
`gui/extension/CHANGELOG.md`.

The target version must match `YYYY.M.P+X.Y.Z`, for example
`2026.4.0+9.7.0`.

The current version is the first `## Changes in ...` entry in
`gui/extension/CHANGELOG.md`.

The previous version is the second `## Changes in ...` entry.

## Rules

- Keep git commands sequential. Do not execute git commands in parallel.
- Do not hardcode the exact files to update for the discover-driven version
  replacement steps. Discover candidate files from the repository layout and
  then filter to files that actually contain the current version string or the
  current `YYYY.M.P` portion.
- Use these repository patterns when discovering candidate files:
  - `general.py`
  - `VERSION`
  - `package.json`
  - `README.md`
  - `.html` files under a `docs` directory
- In upgrade mode, also inspect
  `migration_plugin/lib/backend/remote_helper.py`. The RPM filenames in
  `k_repo_mysqlsh_url` must begin with `mysql-shell-X.Y.Z-`, where `X.Y.Z` is
  the shell-version suffix from the version being summarized, which is the
  current top changelog version before the new stub is added.
- Treat `gui/extension/CHANGELOG.md` as manual-only. Do not include it in
  `discover`; update it only in the dedicated changelog step for the workflow.
- When updating versioned files, replace either:
  - full matches of the previous version, or
  - just the `YYYY.M.P` part
- Follow the mode-specific step order exactly.

## Helper Script

This skill bundles
`./scripts/version_bump_helper.py`.

Useful commands:

```bash
python3 .agents/skills/shell-plugins-version-bump/scripts/version_bump_helper.py versions
python3 .agents/skills/shell-plugins-version-bump/scripts/version_bump_helper.py discover --target 2026.4.0+9.7.0
python3 .agents/skills/shell-plugins-version-bump/scripts/version_bump_helper.py commits
python3 .agents/skills/shell-plugins-version-bump/scripts/version_bump_helper.py check-remote-helper
python3 .agents/skills/shell-plugins-version-bump/scripts/version_bump_helper.py check-head-commit
```

`versions` prints the current and previous changelog versions.

`discover` lists the files matched by the repository patterns that contain the
full current version, the full previous version, or the corresponding
`YYYY.M.P` part, plus the replacement that should be applied. It intentionally
excludes `gui/extension/CHANGELOG.md`.

`commits` gathers non-merge commits from the commit that introduced the
previous changelog entry to `HEAD`, because the current top entry is the
upcoming release stub. It flags obvious maintenance commits and suggests
whether each remaining commit belongs to `### Additions` or `### Fixes`.

`check-remote-helper` validates
`migration_plugin/lib/backend/remote_helper.py` against the version being
summarized in upgrade mode. It reads the current top changelog version and
checks that each `k_repo_mysqlsh_url` RPM filename begins with the expected
`mysql-shell-X.Y.Z-` prefix derived from that version suffix. Update the URLs
manually if the command reports a mismatch.

`check-head-commit` reports whether the latest commit message contains literal
`\n` sequences or lines longer than 72 characters so the commit can be amended
before the workflow is complete.

## Replace Workflow

Run these steps in order:

1. Validate the target version format.
2. Discover files to be modified.
3. Update files to be modified.
4. Replace the top entry in `gui/extension/CHANGELOG.md` with the new version.
5. Commit the changes.
6. Check the resulting commit message for literal `\n` characters and overlong
   lines, and fix it if needed.

Recommended flow:

```bash
python3 .agents/skills/shell-plugins-version-bump/scripts/version_bump_helper.py versions
python3 .agents/skills/shell-plugins-version-bump/scripts/version_bump_helper.py discover --target TARGET_VERSION
```

Then edit only the discovered files. Update the changelog heading from the
current version to the target version. Keep the existing release notes unless
the task explicitly asks to rewrite them.

## Upgrade Workflow

Run these steps in order:

1. Validate the target version format.
2. Gather commits since the previous changelog entry was introduced, because
   the current top entry is the unreleased stub.
3. Update the top entry in `gui/extension/CHANGELOG.md` with summarized
   information for each relevant commit.
4. Ensure
   `migration_plugin/lib/backend/remote_helper.py:k_repo_mysqlsh_url` is up to
   date for the version being summarized. Update stale links, and ensure every
   RPM filename begins with `mysql-shell-X.Y.Z-`, where `X.Y.Z` is the suffix
   from the current top changelog version being finalized in this step.
5. Commit the changelog change, including any needed `remote_helper.py`
   update from the previous step.
6. Check the resulting commit message for literal `\n` characters and overlong
   lines, and fix it if needed.
7. Discover files to be modified.
8. Update files to be modified.
9. Add a stub entry with the new version to `gui/extension/CHANGELOG.md`.
10. Commit the version bump.
11. Check the resulting commit message for literal `\n` characters and
    overlong lines, and fix it if needed.

Use commit subjects and bodies as source material, but rewrite them into a
concise release summary instead of copying them verbatim.

### Commit Selection

- Ignore merge commits.
- Ignore maintenance-only commits such as version bumps, changelog-only
  maintenance, test suite changes, CI-only changes, license updates, and other
  clearly non-user-visible housekeeping.
- Default to one changelog bullet per relevant commit.
- Merge several commits into one bullet only when they clearly describe the
  same user-visible change and the merged bullet preserves the leading `WL#` or
  `BUG#` identifier when one exists.

### Changelog Sections

- Commits whose title begins with `WL#`, ignoring case, go into
  `### Additions`.
- All other relevant commits go into `### Fixes`.
- If the subject begins with `WL#` or `BUG#`, ignoring case, preserve that
  token at the beginning of the bullet.
- Skip an empty section.

For upgrade mode, the new top entry is a stub for the target version, and the
previous top entry becomes the fully summarized release entry for the version
being finalized.

## Commit Message Guidance

Every commit message must follow this format:

1. First line: short imperative summary.
2. Blank line.
3. Body: short full sentences explaining what changed and why.
4. Wrap body lines to 72 characters or less.

After every commit, run `check-head-commit`. If it reports literal `\n`
sequences or overlong lines, amend the commit immediately with a clean
multi-line message whose body is wrapped to 72 characters or less.

## Notes

- Prefer reading the changelog to derive the current and previous versions
  instead of using `README.md`, tags, or hardcoded constants as the source of
  truth.
- Discover version-bearing files from repository patterns plus content matches
  before editing instead of relying on retired helper scripts.
