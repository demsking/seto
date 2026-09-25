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
import posixpath
import shlex

from ..core.context import DockerContextError
from ..core.context import DockerContextResolver
from ..core.shell import File
from ..core.shell import Setting
from ..core.shell import Shell
from ..core.volume import Volume
from ..shells.local import LocalShell
from ..shells.remote import RemoteShell

logger = logging.getLogger(__name__)


# pylint: disable=too-many-public-methods
class Driver:
  def __init__(
    self,
    stack: str,
    *,
    namespace: str,
    host: Shell,
    storage_node: Shell,
    storage_base_path: str = '/mnt/nfs',
    debug: bool = False,
    contexts: DockerContextResolver | None = None,
  ) -> None:
    self.stack: str | None = stack
    self.namespace = namespace
    self.host = host
    self.storage_node = storage_node
    self.storage_base_path = storage_base_path.rstrip('/') or '/'
    self.debug = debug
    self.contexts = contexts if contexts is not None else DockerContextResolver()

  @property
  def shell(self) -> Shell:
    return self.host

  @property
  def brickname(self) -> str:
    return f'{self.namespace}/{self.stack}' if self.stack else self.namespace

  @property
  def stack_id(self) -> str:
    return f'{self.namespace}_{self.stack}' if self.stack else self.namespace

  @property
  def slug(self) -> str:
    raise NotImplementedError()

  @property
  def sheme(self) -> str:
    return f'{self.slug}://'

  @property
  def driver_name(self) -> str:
    raise NotImplementedError()

  @property
  def brick(self) -> str:
    return posixpath.join(self.storage_base_path, self.brickname)

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
    self.storage_node.connect()

  def provision_docker_access(self, node: Shell, username: str | None) -> None:
    """Grant an administrative SSH user access to the node Docker daemon.

    Docker operations no longer run over SSH: they go through the Docker context
    created by ``seto setup``. This host-level step only makes that context
    usable, by authorizing the generated SSH key for the administrative user and
    by ensuring the user may reach the Docker daemon.

    No dedicated ``setouser`` account is created.
    """
    if node.setting.local:
      return

    user = (username or node.setting.username or '').strip()

    if not user:
      raise DockerContextError(
        f"Node '{node.hostname}' has no SSH username; a Docker context for a remote node "
        f'requires an administrative SSH user'
      )

    user_home = self.get_user_home(node, user)
    ssh_dir = f'{user_home}/.ssh'

    node.mkdir(ssh_dir, user=user, group=user, mode='700')

    if node.ssh_pub_key:
      authorized_keys_file = f'{ssh_dir}/authorized_keys'
      node.run(f'touch {shlex.quote(authorized_keys_file)}', sudo=True, quiet=True)
      authorized_keys = node.file(authorized_keys_file)

      if authorized_keys.append(node.ssh_pub_key):
        logger.info('Authorized the SSH key of %s on node %s.', user, node.hostname)

      authorized_keys.chown(user, user)
      authorized_keys.chmod('600')
    else:
      logger.warning(
        'No SSH public key found for %s; Docker contexts target remote nodes through SSH, so '
        'key-based authentication is required.',
        user,
      )

    self.ensure_docker_group_access(node, user)

  def ensure_docker_group_access(self, node: Shell, user: str) -> None:
    """Ensure the SSH user may use the Docker daemon of a remote node."""
    try:
      node.run('docker info', quiet=True, stdout=False)
      return
    except Exception:  # pylint: disable=broad-except
      pass

    logger.info('Granting Docker daemon access to %s on node %s...', user, node.hostname)

    try:
      node.run(f'usermod -aG docker {shlex.quote(user)}', sudo=True, quiet=True)
    except Exception as error:  # pylint: disable=broad-except
      logger.warning(
        'Unable to add %s to the "docker" group on node %s: %s', user, node.hostname, error
      )

  @staticmethod
  def get_user_home(node: Shell, user: str) -> str:
    # Resolve the user home from passwd to support distro-specific defaults.
    user_home = node.run(
      f'getent passwd {shlex.quote(user)} | cut -d: -f6',
      sudo=True,
      quiet=True,
    ).strip()

    if user_home in ('', '/', '/nonexistent'):
      user_home = f'/home/{user}'

    return user_home

  def setup_storage_node(self, storage_replicas: list[Setting], force: bool = False) -> None:
    self.provision_docker_access(self.storage_node, self.storage_node.setting.username)

  def apply_storage_node_changes(self) -> None:
    raise NotImplementedError()

  @staticmethod
  def get_shell(setting: Setting, key_file_path: str | None = None) -> Shell:
    if setting.local:
      return LocalShell(setting, key_file_path)

    return RemoteShell(setting, key_file_path)

  def setup_nodes(self, clients: list[Setting], force: bool = False) -> None:
    for node_setting in clients:
      logger.info('\nConnecting to %s node %s...', self.driver_name, node_setting.hostname)

      shell = Driver.get_shell(node_setting, self.storage_node.key_file_path)
      shell.connect()

      logger.info('\nSetting %s client %s...', self.driver_name, node_setting.hostname)
      self.provision_docker_access(shell, node_setting.username)
      self.setup_node(shell, force)

  def setup_node(self, shell: Shell, force: bool = False) -> None:
    raise NotImplementedError()

  def create_volumes(
    self, *, volumes: list[Volume], sync: bool = False, force: bool = False
  ) -> None:
    raise NotImplementedError()

  def mount_volumes(self, *, volumes: list[Volume]) -> None:
    if len(volumes) > 0:
      node = self.host
      fstab = node.file('/etc/fstab')
      self.mount_node_volumes(node, volumes, fstab)

  def mount_node_volumes(
    self,
    node: Shell,
    volumes: list[Volume],
    fstab: File,
  ) -> None:
    for volume in volumes:
      logger.info('\nMounting volume %s:%s', node.hostname, self.mount_point(volume.mount_folder))
      self.mount_volume(node, volume, fstab)

  def mount_volume(self, node: Shell, volume: Volume, fstab: File) -> None:
    volume_storage = self.storage(volume)
    volume_device = self.mount_point(volume.mount_folder)

    node.mkdir(volume_device)
    self.mount(node, volume_storage, device=volume_device, fstab=fstab)
    node.run(f'df -h {volume_device}')

  def mount(self, node: Shell, storage: str, *, device: str, fstab: File) -> None:
    raise NotImplementedError()

  def umount_volumes(self, *, volumes: list[Volume]) -> None:
    if len(volumes) > 0:
      node = self.host
      self.umount_node_volumes(node, volumes)

  def umount_node_volumes(self, node: Shell, volumes: list[Volume]) -> None:
    for volume in volumes:
      logger.info('\nUnmounting volume %s:%s', node.hostname, self.mount_point(volume.mount_folder))
      self.umount(node, volume.mount_folder)

  def umount(self, node: Shell, device: str) -> None:
    node.run(f'umount {device}', sudo=True)

  def resolve_compose_volume(self, volume: Volume) -> dict:
    raise NotImplementedError()

  def terminate(self) -> None:
    # Close the SSH connection
    self.storage_node.close()
