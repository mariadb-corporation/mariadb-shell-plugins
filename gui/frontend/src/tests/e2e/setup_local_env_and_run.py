# Copyright (c) 2023, 2026, Oracle and/or its affiliates.
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
# separately licensed software that they have included with
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

import argparse
import os
import pathlib
import subprocess
import tempfile
import typing
import task_utils
import socket
import shutil
import sys

arg_parser = argparse.ArgumentParser()

arg_parser.add_argument(
    "-t",
    "--test_to_run",
    required=False,
    type=str,
    default=os.environ["TEST_TO_RUN"] if "TEST_TO_RUN" in os.environ else "ALL",
    help="Specify test name to run",
)

arg_parser.add_argument(
    "--headless",
    required=False,
    type=str,
    default=os.environ["HEADLESS"] if "HEADLESS" in os.environ else "1",
    help="Run in headless mode",
)

arg_parser.add_argument(
    "--db_port",
    required=False,
    type=str,
    default=os.environ["DB_PORT"] if "DB_PORT" in os.environ else "2208",
    help="Specify db port",
)

arg_parser.add_argument(
    "--db_root_user",
    required=False,
    type=str,
    default=os.environ["DB_ROOT_USER"] if "DB_ROOT_USER" in os.environ else "root",
    help="Specify db root user name",
)

arg_parser.add_argument(
    "--db_root_password",
    required=False,
    type=str,
    default=os.environ["DB_ROOT_PASSWORD"] if "DB_ROOT_PASSWORD" in os.environ else "",
    help="Specify db root user password",
)

arg_parser.add_argument(
    "--execution_mode",
    required=False,
    type=str,
    default="full",
    help="Specify the execution mode full, setup, execute, teardown",
)

try:
    argv = arg_parser.parse_args()
except argparse.ArgumentError as e:
    print(str(e))

DB_ROOT_PASSWORD = "" if argv.db_root_password == "-" else argv.db_root_password
TEXT_ONLY_OUTPUT = True

WORKING_DIR = os.path.abspath(os.path.dirname(__file__))
SAKILA_SQL_PATH = os.path.join(WORKING_DIR, "sql", "sakila_cst.sql")
WORLD_SQL_PATH = os.path.join(WORKING_DIR, "sql", "world_x_cst.sql")
USERS_PATH = os.path.join(WORKING_DIR, "sql", "users.sql")
PROCEDURES_PATH = os.path.join(WORKING_DIR, "sql", "procedures.sql")
TESTS_TIMEOUT = 30 * 60


class SetEnvironmentVariablesTask:
    """Task for setting environment variables"""

    def __init__(
        self, environment: typing.Dict[str, str], dir_name: str, servers: typing.List
    ) -> None:
        self.environment = environment
        self.dir_name = dir_name
        self.servers = servers

    def run(self) -> None:
        """Runs the task"""

        self.environment["DBUSERNAME"] = argv.db_root_user
        self.environment["DBPASSWORD"] = DB_ROOT_PASSWORD

        if self.environment["EXECUTION_MODE"] in ["teardown", "execute"]:
            return

        data = task_utils.get_configuration()

        data["PORT_POOL"] = []
        for server in self.servers:
            if server.multi_user:
                data["SHELL_UI_MU_PORT"] = server.port
            elif server.single_server:
                data["SHELL_UI_SS_PORT"] = server.port
            else:
                data["PORT_POOL"].append(server.port)

        data["SQLITE_PATH_FILE"] = os.path.join(
            self.dir_name,
            "mariadb-shell",
            "plugin_data",
            "gui_plugin",
            "mysqlsh_gui_backend.sqlite3",
        )

        task_utils.save_configuration(data)

        task_utils.Logger.success("Environment variables have been set")

    def clean_up(self) -> None:
        """Clean up after task finish"""


class NPMScript:
    """Runs e2e tests"""

    def __init__(
        self, environment: typing.Dict[str, str], script_name: str, params: typing.List
    ) -> None:
        self.environment = environment
        self.script_name = script_name
        self.params = params

    def run(self) -> None:
        """Runs the task"""

        args = [task_utils.get_executables("npm"), "run", self.script_name]
        if argv.test_to_run.upper() != "ALL":
            args.append("-t")
            args.append(argv.test_to_run)

        if self.params is not None:
            args.append("--")
            for param in self.params:
                args.append(param)

        e2e_tests = subprocess.Popen(args=args, env=self.environment)
        e2e_tests.communicate()

        try:
            return_code = e2e_tests.wait(timeout=TESTS_TIMEOUT)
        except subprocess.TimeoutExpired:
            e2e_tests.kill()
            return_code = -1

        if return_code != 0:
            raise task_utils.TaskFailException("Tests failed")

    def clean_up(self) -> None:
        """Clean up after task finish"""


class SetFrontendTask:
    """Setup for the FE"""

    def __init__(self, environment: typing.Dict[str, str]) -> None:
        self.environment = environment

    def run(self) -> None:
        """Runs the task"""

        node_modules_path = pathlib.Path(WORKING_DIR, "..", "..", "..", "node_modules")
        build_path = pathlib.Path(WORKING_DIR, "..", "..", "..", "build")
        webroot_path = pathlib.Path(
            WORKING_DIR,
            "..",
            "..",
            "..",
            "..",
            "backend",
            "gui_plugin",
            "core",
            "webroot",
        )

        if not node_modules_path.is_dir():
            self.install_npm_modules()
            task_utils.Logger.success("Npm modules installed successfully")

        if not build_path.is_dir():
            self.build_frontend()
            task_utils.Logger.success("Frontend built successfully")

        if not webroot_path.is_symlink():
            task_utils.create_symlink(build_path.resolve(), webroot_path.resolve())
            task_utils.Logger.success("Webroot path created successfully")

    def install_npm_modules(self) -> None:
        """Installs the npm modules"""

        args = [task_utils.get_executables("npm"), "install"]
        npm_modules = subprocess.Popen(args=args, env=self.environment)

        npm_modules.communicate()
        npm_modules.wait()

    def build_frontend(self) -> None:
        """Builds the frontend"""

        if os.name == "nt":
            args = [task_utils.get_executables("npm"), "run", "build-win"]
        else:
            args = [task_utils.get_executables("npm"), "run", "build"]

        npm_modules = subprocess.Popen(args=args, env=self.environment)
        npm_modules.communicate()
        npm_modules.wait()

    def clean_up(self) -> None:
        """Clean up after task finish"""


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def get_be_servers(environment):
    be_servers = []

    if environment["EXECUTION_MODE"] in ["setup", "full"]:
        server_port = 8000
        while len(be_servers) < 13:
            if not is_port_in_use(server_port):
                if len(be_servers) < 11:
                    be_servers.append(task_utils.BEServer(environment, server_port))
                elif len(be_servers) < 12:
                    be_servers.append(
                        task_utils.BEServer(environment, server_port, True)
                    )
                else:
                    be_servers.append(
                        task_utils.BEServer(environment, server_port, False, True)
                    )

            server_port += 1
    else:
        ports = []

        data = task_utils.get_configuration()
        ports = data["PORT_POOL"]
        ports.append(data["SHELL_UI_MU_PORT"])
        ports.append(data["SHELL_UI_SS_PORT"])

        # No checks are needed, as these are used only to kill the processes
        for port in ports:
            try:
                be_servers.append(
                    task_utils.BEServer(environment, port, start_process=False)
                )
            except RuntimeError as err:
                task_utils.Logger.warning(str(err))

    return be_servers


def main() -> None:
    test_failed = False
    dev_path = None
    if argv.execution_mode != "full":
        this_path = os.path.dirname(__file__)
        dev_path = os.path.join(this_path, "execution")
        if argv.execution_mode == "setup":
            if os.path.exists(dev_path):
                shutil.rmtree(dev_path)
            os.mkdir(dev_path)

    with tempfile.TemporaryDirectory() as random_path:
        tmp_dirname = random_path if dev_path is None else dev_path

        executor = task_utils.TaskExecutor(tmp_dirname)

        executor.environment["EXECUTION_MODE"] = argv.execution_mode

        executor.add_prerequisite(task_utils.CheckVersionTask("MySQL Shell"))
        executor.add_prerequisite(task_utils.CheckVersionTask("MySQL Server"))
        executor.add_prerequisite(task_utils.CheckVersionTask("Chrome browser"))
        executor.add_prerequisite(task_utils.CheckVersionTask("npm"))
        executor.add_prerequisite(task_utils.CheckVersionTask("ChromeDriver"))

        be_servers = get_be_servers(executor.environment)

        executor.add_task(
            SetEnvironmentVariablesTask(executor.environment, tmp_dirname, be_servers)
        )

        if argv.execution_mode in ["setup", "full"]:
            executor.add_task(SetFrontendTask(executor.environment))

            executor.add_task(
                task_utils.SetPluginsTask(
                    pathlib.Path(tmp_dirname, "mariadb-shell", "plugins"), be_servers
                )
            )

            executor.add_task(
                task_utils.AddUserToBE(executor.environment, tmp_dirname, be_servers)
            )

            executor.add_task(task_utils.ClearCredentials(executor.environment))

        if argv.execution_mode != "execute":
            executor.add_task(
                task_utils.StartBeServersTask(executor.environment, be_servers)
            )

            executor.add_task(
                task_utils.SetMySQLServerTask(
                    executor.environment, tmp_dirname, argv.db_port, True
                )
            )

            executor.add_task(
                task_utils.SetMySQLServerTask(executor.environment, tmp_dirname, "2207")
            )

        executor.add_task(task_utils.DisableTests)

        data = task_utils.get_configuration()

        if argv.execution_mode in ["full", "execute"]:
            executor.add_task(
                NPMScript(
                    executor.environment,
                    "e2e-tests-run",
                    [f"--maxWorkers={data["MAX_WORKERS"]}"],
                )
            )

        if executor.check_prerequisites():
            try:
                executor.run_tasks()
            except task_utils.TaskFailException as exception:
                task_utils.Logger.error(exception)
                test_failed = True
                # The proper way to handle this internal exception handling
                # is by adding executor state that we can clean up only those tasks
                # that was executed. This is temporary solution and will be
                # reworked eventually.
            executor.clean_up()

    if dev_path is not None and argv.execution_mode == "teardown":
        shutil.rmtree(dev_path)

    if test_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
