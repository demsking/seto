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
from ..core.shell import File
from ..core.shell import Setting
from ..core.shell import Shell
from ..core.volume import Volume
from ..shells.local import LocalShell
from ..shells.remote import RemoteShell


class Driver:
  setouser = 'setouser'

  def __init__(
    self,
    stack: str,
    *,
    project: str,
    brickname: str,
    shell: Shell,
  ) -> None:
    self.stack = stack
    self.project = project
    self.brickname = brickname
    self.shell = shell

  @property
  def slug(self) -> str:
    raise NotImplementedError()

  @property
  def sheme(self) -> str:
    raise f'{self.slug}://'

  @property
  def driver_name(self) -> str:
    raise NotImplementedError()

  @property
  def brick(self) -> str:
    return f'/data/{self.slug}/{self.brickname}'

  @property
  def mount_point_prefix(self) -> str:
    return f'/mnt/{self.slug}-{self.brickname}'

  def mount_point(self, mount_folder: str | None = None) -> str:
    if mount_folder:
      return f'{self.mount_point_prefix}-{mount_folder}'

    return self.mount_point_prefix

  def storage(self, volume: Volume) -> str:
    return f'{self.brick}/{volume.mount_folder}'

  def connect(self) -> None:
    self.shell.connect()

  def create_seto_user(self, node: Shell) -> None:
    # if node.check_user_exists(self.setouser):
    #   node.run(f'deluser --system --remove-home {self.setouser}', quiet=True)

    if not node.check_user_exists(self.setouser):
      # node.run(f'adduser {self.setouser} --quiet --system --disabled-login --disabled-password --ingroup docker', quiet=not True)
      node.run(f'useradd --system -m -U {self.setouser}', quiet=True)
      node.run(f'usermod -aG docker {self.setouser}', quiet=True)
      node.run(f'id {self.setouser}', quiet=True)

      # Create the .ssh directory if it doesn't exist
      ssh_home = f'/home/{self.setouser}/.ssh'
      authorized_keys_file = f'{ssh_home}/authorized_keys'

      node.mkdir(ssh_home, user=self.setouser, group=self.setouser, mode='700')
      node.run(f'touch {authorized_keys_file}', quiet=True)
      authorized_keys = node.file(authorized_keys_file)

      authorized_keys.append(node.ssh_pub_key)
      authorized_keys.chown(self.setouser, self.setouser)
      authorized_keys.chmod('600')

  def setup_manager(self, replica: list[Setting], force: bool = False) -> None:
    self.create_seto_user(self.shell)

  def apply_manager_changes(self) -> None:
    raise NotImplementedError()

  @staticmethod
  def get_shell(setting: Setting, key_file_path: str | None = None) -> Shell:
    if setting.local:
      return LocalShell(setting, key_file_path)

    return RemoteShell(setting, key_file_path)

  def setup_nodes(self, clients: list[Setting], force: bool = False) -> None:
    for node_setting in clients:
      print(f'\nConnecting to {self.driver_name} node {node_setting.hostname}...')

      shell = Driver.get_shell(node_setting, self.shell.key_file_path)
      shell.connect()

      print(f'\nSetting {self.driver_name} client {node_setting.hostname}...')
      self.create_seto_user(shell)
      self.setup_node(shell, force)

  def setup_node(self, shell: Shell, force: bool = False) -> None:
    raise NotImplementedError()

  def create_volumes(
    self,
    *,
    replica: list[Setting],
    volumes: list[Volume],
    force: bool = False,
  ) -> None:
    raise NotImplementedError()

  def mount_volumes(self, *, clients: list[str], volumes: list[Volume]) -> None:
    if len(volumes) > 0:
      for node_setting in clients:
        node = Driver.get_shell(node_setting, self.shell.key_file_path)
        node.connect()
        fstab = node.file('/etc/fstab')
        self.mount_node_volumes(node, volumes, fstab)
        node.close()

  def mount_node_volumes(
    self,
    node: Shell,
    volumes: list[Volume],
    fstab: File,
  ) -> None:
    for volume in volumes:
      print(f'\nMounting volume {node.hostname}:{self.mount_point(volume.mount_folder)}')
      self.mount_volume(node, volume, fstab)

  def mount_volume(self, node: Shell, volume: Volume, fstab: File) -> None:
    volume_storage = self.storage(volume)
    volume_device = self.mount_point(volume.mount_folder)

    node.mkdir(volume_device)
    self.mount(node, volume_storage, device=volume_device, fstab=fstab)
    node.run(f'df -h {volume_device}')

  def mount(self, node: Shell, storage: str, *, device: str, fstab: File) -> None:
    raise NotImplementedError()

  def umount_volumes(self, *, clients: list[str], volumes: list[Volume]) -> None:
    if len(volumes) > 0:
      for node_setting in clients:
        node = Driver.get_shell(node_setting, self.shell.key_file_path)
        node.connect()
        self.umount_node_volumes(node, volumes)
        node.close()

  def umount_node_volumes(self, node: Shell, volumes: list[Volume]) -> None:
    for volume in volumes:
      print(f'\nUnmounting volume {node.hostname}:{self.mount_point(volume.mount_folder)}')
      self.umount(node, volume)

  def umount(self, node: Shell, device: str) -> None:
    node.run(f'umount {device}')

  def resolve_compose_volume(self, volume: Volume) -> dict:
    raise NotImplementedError()

  def terminate(self):
    # Close the SSH connection
    self.shell.close()
