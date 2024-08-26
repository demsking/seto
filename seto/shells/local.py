# Copyright 2024 Sébastien Demanou. All Rights Reserved.
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
import subprocess
import sys

from ..core.shell import Shell


class LocalShell(Shell):
  _password = None

  def connect(self):
    pass

  def run(
    self,
    cmd: str,
    *,
    sudo=True,
    stdout=True,
    stderr=False,
    quiet=False,
  ) -> str:
    if sudo:
      cmd = f"sudo sh -c '{cmd}'"

    if not quiet:
      print(f'{self.prompt} {cmd}')

    process = subprocess.Popen(
      cmd,
      shell=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
    )

    std_output, stderr = process.communicate()
    stdout_output = std_output.decode('utf-8')
    stderr_output = stderr.decode('utf-8')

    if stdout_output and stdout:
      print(stdout_output)

    if stderr_output:
      if stderr:
        raise Exception(stderr_output)

      print(stderr_output)
      sys.exit(1)

    return stdout_output

  def copy_file(self, *, local_path: str, remote_path: str) -> None:
    print(f'{self.prompt} cp {local_path} {remote_path}')
    self.run(f'cp {local_path} {remote_path}')

  def close(self) -> None:
    pass
