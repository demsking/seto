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
import sys

import paramiko

from ..core.shell import Setting
from ..core.shell import Shell


class RemoteShell(Shell):
  _fs: paramiko.SFTPClient

  def __init__(self, setting: Setting, key_file_path: str | None = None) -> None:
    super().__init__(setting, key_file_path)

    self.ssh = paramiko.SSHClient()

  def connect(self):
    self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # self.ssh.load_system_host_keys()

    private_key = None

    if self.key_file_path:
      private_key = paramiko.RSAKey.from_private_key_file(self.key_file_path)

    print(f'\nConnecting to {self.setting.username}@{self.setting.hostname}...')
    self.ssh.connect(
      hostname=self.setting.hostname,
      username=self.setting.username,
      password=self.setting.password,
      pkey=private_key,
    )

    self._fs = self.ssh.open_sftp()

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

    _stdin, std_output, stderr = self.ssh.exec_command(cmd)
    stdout_output = std_output.read().decode('utf-8')
    stderr_output = stderr.read().decode('utf-8')

    if stdout_output and stdout:
      print(stdout_output)

    if stderr_output:
      if stderr:
        raise Exception(stderr_output)

      print(stderr_output)
      sys.exit(1)

    return stdout_output

  def copy_file(self, *, local_path: str, remote_path: str) -> None:
    print(f'{self.prompt} scp {local_path} {self.hostname}:{remote_path}')
    self._fs.put(local_path, remote_path)

  def close(self) -> None:
    self.ssh.close()
