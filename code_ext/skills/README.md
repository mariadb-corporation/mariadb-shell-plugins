# Skills for Copilot

The MariaDB agent skills this extension contributes to GitHub Copilot, through
`contributes.chatSkills` in `package.json`.

**Vendored - do not edit.** `npm run sync:skills` (run by `npm run build`)
replaces this folder with the `claude/dev-plugin/skills` of the newest
[mariadb-corporation/ai-plugins](https://github.com/mariadb-corporation/ai-plugins/releases) release, and
rewrites `chatSkills` to match. `skills-source.json` records which release that
was, and where its skills came from. To change a skill, change it at its
source.

Skills vendored from `mariadb-corporation/mariadb-docs` are licensed as that
repository says (its `agent-skills` are MIT); those from ai-plugins'
`additional-skills/` are GPL-2.0, as this extension is.
