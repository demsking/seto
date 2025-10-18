# Copyright 2024-2025 Sébastien Demanou. All Rights Reserved.
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

    fd = self.ssh.open_sftp()

    if fd:
      self._fs = fd

  def run(
    self,
    command: str,
    *,
    sudo=False,
    stdout=True,
    stderr=False,
    quiet=False,
    input: str | None = None,
  ) -> str:
    if sudo:
      command = f"sudo sh -c '{command}'"

    if not quiet:
      self.print(command)

    stdin, std_output, stderr_output = self.ssh.exec_command(command)

    if input:
      assert stdin is not None
      stdin.write(input)
      stdin.flush()
      stdin.channel.shutdown_write()  # very important to close stdin for EOF

    stdout_output = std_output.read().decode('utf-8')
    stderr_output = stderr_output.read().decode('utf-8')

    if stdout_output and stdout:
      print(stdout_output)

    if stderr_output:
      if stderr:
        raise Exception(stderr_output)

      print(stderr_output)

      if stderr:
        sys.exit(1)

    return stdout_output

  def copy_file(self, *, local_path: str, remote_path: str) -> None:
    self.print(f'scp {local_path} {self.hostname}:{remote_path}')
    self._fs.put(local_path, remote_path)

  def close(self) -> None:
    self.ssh.close()
