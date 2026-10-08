#!/bin/bash
# Copyright (c) 2023, 2025, Oracle and/or its affiliates.
# Copyright (c) 2026, MariaDB plc.

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
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

# Runs the MRS grammar test (grammar/test/grammar_test.sql) against a MariaDB
# sandbox deployed for the run, and removes the sandbox again afterwards.
#
# Needs a mariadbd binary on the PATH (the sandbox is deployed from it) and
# the MariaDB Shell, taken from MARIADB_SHELL or found on the PATH. The shell
# loads this repository's mrs_plugin and msm_plugin through a temporary user
# config home, not whatever ~/.mariadb-shell holds.
#
# Exits non-zero when a statement of the grammar test fails. With
# SLEEP_ON_ERROR=1 the output pauses for five seconds at each error.
# MARIADB_SHELL_OPTIONS is added to every shell call, for example
# MARIADB_SHELL_OPTIONS=--disable-modules=mrs on a shell whose built-in mrs
# module would clash with this plugin.

set -u

# The grammar test refers to its files relative to the plugin directory.
cd "$(dirname "$0")/.." || exit 1

SHELL_BIN="${MARIADB_SHELL:-mariadb-shell}"
# Word-split on purpose: it may hold several options.
SHELL_OPTS=(${MARIADB_SHELL_OPTIONS:-})
if ! command -v "$SHELL_BIN" ${SHELL_OPTS[@]+"${SHELL_OPTS[@]}"} > /dev/null; then
    echo "Could not find the MariaDB Shell binary. Set MARIADB_SHELL or put mariadb-shell on the PATH." >&2
    exit 1
fi
if ! command -v mariadbd > /dev/null && ! command -v mysqld > /dev/null; then
    echo "No mariadbd or mysqld binary on the PATH: the grammar test deploys a sandbox and cannot run without one." >&2
    exit 1
fi

SCRATCH="$(mktemp -d -t mrs_grammar_test)"
SANDBOX_DIR="$SCRATCH/sandbox"
LOG="$SCRATCH/grammar_test.log"
PASSWORD="mrs_grammar_test_root"

# The run's own shell home, so the repository's plugins are the ones loaded.
export MARIADB_SHELL_USER_CONFIG_HOME="$SCRATCH/dot_mariadb_shell"
mkdir -p "$MARIADB_SHELL_USER_CONFIG_HOME/plugins"
ln -s "$PWD" "$MARIADB_SHELL_USER_CONFIG_HOME/plugins/mrs_plugin"
ln -s "$PWD/../msm_plugin" "$MARIADB_SHELL_USER_CONFIG_HOME/plugins/msm_plugin"
# The shell's own colouring would get in the way of the error matching below.
export MARIADB_SHELL_TERM_COLOR_MODE=nocolor
# A throw-away test server: skipping the syncs makes its DDL much faster.
export MARIADB_SANDBOX_NO_SYNC="${MARIADB_SANDBOX_NO_SYNC:-1}"

PORT="$("$SHELL_BIN" ${SHELL_OPTS[@]+"${SHELL_OPTS[@]}"} --py -e 'import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])')"

cleanup() {
    "$SHELL_BIN" ${SHELL_OPTS[@]+"${SHELL_OPTS[@]}"} --py -e "
for operation in (sandbox.stop, sandbox.kill, sandbox.delete):
    try:
        operation($PORT, {'sandboxDir': '$SANDBOX_DIR'})
    except Exception:
        pass" > /dev/null 2>&1
    rm -rf "$SCRATCH"
}
trap cleanup EXIT

echo "Deploying a MariaDB sandbox on port $PORT ..."
"$SHELL_BIN" ${SHELL_OPTS[@]+"${SHELL_OPTS[@]}"} --py -e "sandbox.deploy($PORT, {'password': '$PASSWORD', 'sandboxDir': '$SANDBOX_DIR', 'ssl': False})" || exit 1

URI="root:$PASSWORD@127.0.0.1:$PORT"

# The test schemas: sakila (schema only, no data is needed) and the setup's
# own objects. A failure here is a failure of the run, not of the grammar.
"$SHELL_BIN" ${SHELL_OPTS[@]+"${SHELL_OPTS[@]}"} "$URI" --sql -f ./grammar/test/sakila-schema.sql || exit 1
"$SHELL_BIN" ${SHELL_OPTS[@]+"${SHELL_OPTS[@]}"} "$URI" --sql -f ./grammar/test/grammar_test_setup.sql || exit 1

function check_errors() {
    grep --color=always -e ^ -e 'Syntax.*' -e 'Error:.*' -e '^ERROR.*' | while read line
    do
        echo "$line"
        if echo $line|grep '^.\[01;31m' > /dev/null; then
            sleep 5
        fi
    done
}

if test "${SLEEP_ON_ERROR:-0}" == 1; then
    color=check_errors
else
    color="grep --color=always -e ^ -e Syntax.* -e Error:.* -e ^ERROR.*"
fi

# --interactive=full keeps going after a failed statement, so the whole file
# is exercised and every error is in the log.
"$SHELL_BIN" ${SHELL_OPTS[@]+"${SHELL_OPTS[@]}"} "$URI" --sql --interactive=full --log-level=debug3 --verbose=4 -f ./grammar/test/grammar_test.sql 2>&1 | tee "$LOG" | $color

# The shell's verbose lines include errors the plugin handles itself (a REVOKE
# of a grant that was never given, say); only the statements' own results
# count.
ERRORS="$(grep -v '^verbose:' "$LOG" | grep -c -e 'Syntax' -e '^ERROR')"
if test "$ERRORS" != 0; then
    echo
    echo "MRS grammar test FAILED: $ERRORS statement(s) reported an error."
    exit 1
fi

echo
echo "MRS grammar test passed."
