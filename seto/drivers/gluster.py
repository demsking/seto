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
from ..core.driver import Driver
from ..core.shell import File
from ..core.shell import Setting
from ..core.shell import Shell
from ..core.volume import Volume


class GlusterDriver(Driver):
  @property
  def slug(self) -> str:
    return 'glusterfs'

  @property
  def driver_name(self) -> str:
    return 'GlusterFS'

  @staticmethod
  def install_plugin(shell: Shell, force: bool = False) -> None:
    """
    Install GlusterFS Volume Plugin.

    Images:
      - https://registry.hub.docker.com/r/mochoa/glusterfs-volume-plugin
      - https://registry.hub.docker.com/r/mochoa/glusterfs-volume-plugin-aarch64
    """
    uname = shell.run('uname -a', stdout=False)
    plugins_list = shell.run(
      cmd='docker plugin ls | grep glusterfs',
      stdout=False,
      quiet=True,
    )

    if 'glusterfs' in plugins_list:
      if 'true' in plugins_list and not force:
        # Skip install if already installed
        return

      shell.run('docker plugin disable glusterfs')
      shell.run('docker plugin rm glusterfs')

    if 'aarch64' in uname:
      image = 'mochoa/glusterfs-volume-plugin-aarch64'
    else:
      image = 'mochoa/glusterfs-volume-plugin'

    shell.run(f'docker plugin install --alias glusterfs {image} --grant-all-permissions --disable')
    shell.run('docker plugin enable glusterfs')
    shell.run('docker plugin ls')

  @staticmethod
  def install_server(shell: Shell) -> None:
    # Add the user to the nogroup group
    shell.run(f'usermod -aG nogroup {shell.username}')

    fresh_installed = shell.install('glusterfs-server')

    if fresh_installed:
      shell.run('systemctl start glusterd')
      shell.run('systemctl enable glusterd')

  def setup_manager(self, replica: list[Setting], force: bool = False) -> None:
    super().setup_manager(replica, force)

    # Add the user to the nogroup group
    GlusterDriver.install_server(self.shell)
    GlusterDriver.install_plugin(self.shell, force)

    for node_setting in replica:
      shell = Driver.get_shell(node_setting, self.shell.key_file_path)

      shell.connect()
      GlusterDriver.install_server(shell)
      GlusterDriver.install_plugin(shell, force)
      shell.close()

    for node_setting in replica:
      self.shell.run(f'gluster peer probe {node_setting.hostname}')

    self.shell.run('gluster peer status')
    self.shell.run('gluster pool list')

  def apply_manager_changes(self) -> None:
    pass

  def setup_node(self, shell: Shell, force: bool = False) -> None:
    shell.install('glusterfs-client')
    GlusterDriver.install_plugin(shell, force)

  def create_volumes(
    self,
    *,
    replica: list[Setting],
    volumes: list[Volume],
    force: bool = False,
  ) -> None:
    self.shell.mkdir(self.brick)

    replica_count = len(replica)
    replica_items = [f'{item.hostname}:{self.brick}' for item in replica]
    replica_items_str = ' '.join(replica_items)

    for volume in volumes:
      self.shell.run(f'gluster volume create {volume.name} replica {replica_count} transport tcp {replica_items_str} force')
      self.shell.run(f'gluster volume start {volume.name}')

    self.shell.run('gluster volume info')

    for volume in volumes:
      print(f'Initializing volume {volume.name}...')
      self.server.copy_volume(volume, self.brick)

  def mount_volume(self, node: Shell, volume: Volume, fstab: File) -> None:
    """
    Mount the given volume on the given node.

    See https://docs.gluster.org/en/latest/Administrator-Guide/Setting-Up-Clients/#mounting-volumes
    """
    volume_id = f'{self.shell.setting.ip}:/{volume.name}'

    node.mkdir(volume.device)
    node.run(f'mount -t glusterfs {volume_id} {volume.device}')
    node.run(f'df -h {volume.device}')
    fstab.append(f'{volume_id}\t{volume.device}\tglusterfs\tdefaults,_netdev 0 0')

  def resolve_compose_volume(self, volume: Volume) -> dict:
    return {
      'name': volume.name + volume.mount_point,
      'driver': 'glusterfs',
      'driver_opts': {
        'servers': self.shell.hostname,
      },
    }
