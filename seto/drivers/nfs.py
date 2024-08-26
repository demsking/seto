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
from uuid import uuid4

from ..core.driver import Driver
from ..core.shell import File
from ..core.shell import Setting
from ..core.shell import Shell
from ..core.volume import Volume


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

  def setup_manager(self, replica: list[Setting], force: bool = False) -> None:
    super().setup_manager(replica, force)

    fresh_installed = self.shell.install('nfs-kernel-server')

    if fresh_installed:
      self.shell.run('systemctl start nfs-server')
      self.shell.run('systemctl enable nfs-kernel-server')

  def apply_manager_changes(self) -> None:
    self.shell.run('exportfs -rav')

  def setup_node(self, shell: Shell, force: bool = False) -> None:
    shell.install('nfs-common')

  def create_volumes(
    self,
    *,
    replica: list[Setting],
    volumes: list[Volume],
    force: bool = False,
  ) -> None:
    # Read existing export paths from /etc/exports on the remote server
    exports = self.shell.file('/etc/exports')

    self.add_exports_entry(
      self.project,
      exports=exports,
      options=('rw', 'sync', f'fsid={uuid4()}', 'crossmnt', 'no_subtree_check'),
    )

    for volume in volumes:
      volume_storage = self.storage(volume)
      is_new_entry = self.add_exports_entry(volume_storage, exports=exports)

      if force or is_new_entry:
        print(f'Initializing volume {volume.name}...')
        self.shell.copy_volume(volume, self.brick)
        self.shell.run(f'chown -R nobody:nogroup {self.brick}', stdout=False, quiet=True)

  def add_exports_entry(
    self,
    volume_storage: str,
    *,
    exports: File,
    options=('rw', 'sync', 'no_subtree_check', 'no_root_squash'),
  ) -> bool:
    print(f'\nSetting NFS volume {volume_storage}{options}')
    options_str = ','.join(options)

    self.shell.mkdir(volume_storage)

    if 'letsencrypt' in volume_storage:
      self.shell.run(f'[ -n "$(ls -A x 2>/dev/null)" ] && chmod 600 {volume_storage}/*', stdout=False, quiet=True)

    return exports.append(f'{volume_storage}\t*({options_str})', key=f'{volume_storage}\t')

  def mount_node_volumes(
    self,
    node: Shell,
    volumes: list[Volume],
    fstab: File,
  ) -> None:
    device = self.mount_point()

    print(f'Mounting volume {node.hostname}:{device}')
    node.mkdir(device)
    self.mount(node, self.brick, device=device, fstab=fstab)

  def mount(self, node: Shell, storage: str, *, device: str, fstab: File) -> None:
    host_storage = f'{self.shell.setting.ip}:{storage}'
    node.run(f'mount -t nfs {host_storage} {device}')
    fstab.append(f'{host_storage}\t{device}\tnfs\tauto,nofail,noatime,nolock,intr,tcp,actimeo=1800 0 0')

  def umount_node_volumes(self, node: Shell, volumes: list[Volume]) -> None:
    device = self.mount_point()

    print(f'Unmounting volume {node.hostname}:{device}')
    self.umount(node, device)

  def resolve_compose_volume(self, volume: Volume) -> dict:
    return {
      'driver': 'local',
      'driver_opts': {
        'type': 'nfs',
        'o': f'addr={self.shell.setting.ip},nolock,soft,rw',
        'device': f':{self.storage(volume)}',
      },
    }
