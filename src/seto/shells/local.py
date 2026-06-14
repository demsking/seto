# Copyright 2024-2026 Sébastien Demanou. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
import logging
import subprocess

from ..core.shell import Shell

logger = logging.getLogger(__name__)


class LocalShell(Shell):
  def connect(self):
    pass

  def exec(
    self,
    command: str,
    *,
    sudo=False,
    stdout=True,
    stderr=False,
    quiet=False,
    input: str | None = None,  # pylint: disable=redefined-builtin
    env: dict | None = None,
    check=True,
  ):
    if sudo:
      command = f"sudo sh -c '{command}'"

    env = {**dict(subprocess.os.environ), **(env or {})}  # type: ignore
    shell_exec = env.get('SHELL', '/bin/bash')
    has_input = input is not None

    if not quiet:
      self.print(f"echo '...' | {command}" if input else command)

    process = subprocess.Popen(  # pylint: disable=consider-using-with
      command,
      stdin=subprocess.PIPE if has_input else None,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      text=True,
      shell=has_input,
      env=env,
      executable=shell_exec,
    )

    std_output, stderr_output = process.communicate(input=input)

    if stdout and std_output:
      logger.info(std_output)

    if stderr_output:
      logger.error(stderr_output)

    if check and process.returncode != 0:
      raise Exception(  # pylint: disable=broad-exception-raised
        f"Command '{command}' failed with exit code {process.returncode}"
      )

    if stderr and stderr_output:
      raise Exception(stderr_output)  # pylint: disable=broad-exception-raised

    return std_output

  def run(
    self,
    command: str,
    *,
    sudo=False,
    stdout=True,
    stderr=False,
    quiet=False,
    input: str | None = None,  # pylint: disable=redefined-builtin
    env: dict | None = None,
    check=True,
  ) -> str:
    if sudo:
      command = f"sudo sh -c '{command}'"

    env = {**dict(subprocess.os.environ), **(env or {})}  # type: ignore
    shell_exec = env.get('SHELL', '/bin/bash')

    if not quiet:
      self.print(f"echo '...' | {command}" if input else command)

    result = subprocess.run(
      command,
      input=input,
      capture_output=True,
      text=True,
      shell=True,
      env=env,
      executable=shell_exec,
      check=False,
    )

    std_output = result.stdout.strip()
    stderr_output = result.stderr.strip()

    if stdout and std_output:
      logger.info(std_output)

    if stderr_output:
      logger.error(stderr_output)

    if check and result.returncode != 0:
      raise Exception(  # pylint: disable=broad-exception-raised
        f"Command '{command}' failed with exit code {result.returncode}"
      )

    if stderr and stderr_output:
      raise Exception(stderr_output)  # pylint: disable=broad-exception-raised

    return std_output

  def copy_file(self, *, local_path: str, remote_path: str) -> None:
    self.print(f'cp {local_path} {remote_path}')
    self.run(f'cp {local_path} {remote_path}')

  def close(self) -> None:
    pass
