# Change Log

All notable changes to the "mariadb" extension will be documented in this file.

Check [Keep a Changelog](http://keepachangelog.com/) for recommendations on how to structure this file.

## [Unreleased]

### Added

- Startup check for a MariaDB Shell of at least version 26.9.2, looking at
  the `PATH` first and at the local installation directory second, and
  running the official installer when neither has one. The download and
  extraction are reported in a progress notification.
- The MCP server is started from the located shell with
  `mariadb-shell -- mcp start-server --transport=stdio`, and its output is
  written to the *MariaDB* output channel.
- The **MariaDB: Restart MCP Server** and **MariaDB: Show MCP Server Log**
  commands.

### Changed

- The extension is now bundled with Vite and tested with Vitest.
