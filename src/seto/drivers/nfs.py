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
from uuid import uuid4

from ..core.driver import Driver
from ..core.shell import File
from ..core.shell import Setting
from ..core.shell import Shell
from ..core.volume import Volume

logger = logging.getLogger(__name__)


class NFSDriver(Driver):
  @property
  def slug(self) -> str:
    return 'nfs'

  @property
  def driver_name(self) -> str:
    return 'NFS'

  @property
  def mount_point_prefix(self) -> str:
    return f'/mnt/{self.brickname}'

  def setup_storage_node(self, storage_replicas: list[Setting], force: bool = False) -> None:
    super().setup_storage_node(storage_replicas, force)

    fresh_installed = self.storage_node.install('nfs-kernel-server')

    if fresh_installed:
      self.storage_node.run('systemctl start nfs-server', sudo=True)
      self.storage_node.run('systemctl enable nfs-kernel-server', sudo=True)

  def apply_storage_node_changes(self) -> None:
    self.storage_node.run('exportfs -rav', sudo=True)

  def setup_node(self, shell: Shell, force: bool = False) -> None:
    shell.install('nfs-common')

  def create_volumes(
    self, *, volumes: list[Volume], sync: bool = False, force: bool = False
  ) -> None:
    # Read existing export paths from /etc/exports on the remote server
    exports = self.storage_node.file('/etc/exports')

    self.add_exports_entry(
      f'/{self.namespace}',
      exports=exports,
      options=('rw', 'sync', f'fsid={uuid4()}', 'crossmnt', 'no_subtree_check'),
    )

    for volume in volumes:
      volume_storage = self.storage(volume)
      is_new_entry = self.add_exports_entry(volume_storage, exports=exports)

      if sync or is_new_entry:
        logger.info('Initializing volume %s...', volume.name)
        self.storage_node.copy_volume(volume, self.brick, force=force)
        self.storage_node.run(
          f'chown -R nobody:{self.storage_node.nobody_group} {self.brick}',
          sudo=True,
          stdout=False,
          quiet=True,
        )

  def add_exports_entry(
    self,
    volume_storage: str,
    *,
    exports: File,
    options=('rw', 'sync', 'no_subtree_check', 'no_root_squash'),
  ) -> bool:
    logger.info('\nSetting NFS volume %s%s', volume_storage, options)
    options_str = ','.join(options)

    self.storage_node.mkdir(volume_storage)

    if 'letsencrypt' in volume_storage:
      self.storage_node.run(
        f'find {volume_storage} -type f -exec chmod 600 {{}} + 2>/dev/null || true',
        sudo=True,
        stdout=False,
        quiet=True,
      )

    return exports.append(f'{volume_storage}\t*({options_str})', key=f'{volume_storage}\t')

  def mount_node_volumes(
    self,
    node: Shell,
    volumes: list[Volume],
    fstab: File,
  ) -> None:
    device = self.mount_point()

    logger.info('Mounting volume %s:%s', node.hostname, device)
    node.mkdir(device)
    self.mount(node, self.brick, device=device, fstab=fstab)

  def mount(self, node: Shell, storage: str, *, device: str, fstab: File) -> None:
    host_storage = f'{self.storage_node.setting.ip}:{storage}'
    node.run(f'mount -t nfs {host_storage} {device}', sudo=True)
    fstab.append(f'{host_storage}\t{device}\tnfs\tauto,nofail,noatime,intr,tcp,actimeo=1800 0 0')

  def umount_node_volumes(self, node: Shell, volumes: list[Volume]) -> None:
    device = self.mount_point()

    logger.info('Unmounting volume %s:%s', node.hostname, device)
    self.umount(node, device)

  def resolve_compose_volume(self, volume: Volume) -> dict:
    nfs_options = volume.nfs_options or 'rw,hard,noatime,nodiratime'
    return {
      'driver': 'local',
      'driver_opts': {
        'type': 'nfs',
        'o': f'addr={self.storage_node.setting.ip},{nfs_options}',
        'device': f':{self.storage(volume)}',
      },
    }
