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
import functools
import json
import socket

from docker import DockerClient

from .driver import Driver
from .parser import resolve_env_vars
from .shell import Setting
from .shell import Shell


class Docker:
  def __init__(
    self,
    config: dict,
    driver: Driver,
    client: DockerClient,
  ) -> None:
    self.driver = driver
    self.client = client
    self.config = config
    self.localshell = Driver.get_shell(
      Setting.from_connection_string(self.hostname),
    )

  @property
  def shell(self) -> Shell:
    return self.driver.shell

  @property
  def resolved_config(self) -> str:
    return resolve_env_vars(json.dumps(self.config))

  @property
  def hostname(self) -> str:
    return socket.gethostname()

  @property
  def external_networks(self) -> list[str]:
    return [
      item.attrs['Name']
      for item in self.client.networks.list()
      if item.attrs and item.attrs['Name'].startswith(self.driver.stack_id)
    ]

  def build(self) -> None:
    self.localshell.exec(
      command=f'docker compose -f - -p {self.driver.stack_id} build --no-cache',
      input=self.resolved_config,
    )

  def push(self) -> None:
    self.localshell.exec(
      command=f'docker compose -f - -p {self.driver.stack_id} push',
      input=self.resolved_config,
    )

  def pull(self) -> None:
    self.localshell.exec(
      command=f'docker compose -f - -p {self.driver.stack_id} pull --dry-run --ignore-buildable --policy=always',
      input=self.resolved_config,
    )

  def info(self) -> None:
    raise NotImplementedError()

  def deploy(self) -> None:
    raise NotImplementedError()

  def ps(self) -> None:
    raise NotImplementedError()

  def logs(self) -> None:
    raise NotImplementedError()

  def down(self) -> None:
    raise NotImplementedError()


class DockerCompose(Docker):
  @property
  def placement_hostname(self) -> str | None:
    return self.config.get('x-placement-hostname', None)

  @property
  def placement(self) -> str:
    return self.config.get('x-placement', '')

  @property
  @functools.lru_cache(maxsize=128)
  def nodename(self) -> str:
    if self.placement_hostname:
      return self.placement_hostname

    nodes = self.client.nodes.list()

    label_key, label_value = self.placement.split('==')
    label_key = f'{label_key}'.strip()
    value = f'{label_value}'.strip()

    for node in nodes:
      if node.attrs and node.attrs['Spec']['Labels'].get(label_key) == value:
        return node.attrs['Description']['Hostname']

    raise ValueError(f'Unable to found node for placement "{self.placement}"')

  def _remote_node_run(self, command: str, *, hostname: str, input: str) -> None:
    setting = Setting.from_connection_string(f'{Driver.setouser}@{hostname}')
    node = Driver.get_shell(setting, self.shell.key_file_path)

    node.connect()
    node.run(command, input=input)
    node.close()

  def _exec(self, command: str) -> None:
    if self.hostname == self.nodename:
      self.shell.run(command=command, input=self.resolved_config, split=True)
    else:
      self._remote_node_run(
        command,
        hostname=self.nodename,
        input=self.resolved_config,
      )

  def info(self) -> None:
    if self.driver.debug:
      command = f'docker compose -p {self.driver.stack_id} -f - config'
    else:
      command = f'docker compose -p {self.driver.stack_id} -f - config --no-env-resolution --skip-interpolation'

    self._exec(command)

  def deploy(self) -> None:
    print(f'Deploying on {self.nodename}...')
    self._exec(f'docker compose -p {self.driver.stack_id} -f - up -d --remove-orphans')

  def ps(self) -> None:
    self._exec(f'docker compose -p {self.driver.stack_id} -f - ps')

  def logs(self) -> None:
    self._exec(f'docker compose -p {self.driver.stack_id} -f - logs -n 10')

  def down(self) -> None:
    self._exec(f'docker compose -p {self.driver.stack_id} -f - down')


class DockerSwarm(Docker):
  @property
  def shell(self) -> Shell:
    return Driver.get_shell(
      Setting.from_connection_string(self.hostname),
    )

  def info(self) -> None:
    if self.driver.debug:
      command = 'docker stack config -c -'
    else:
      command = 'docker stack config -c - --skip-interpolation'

    self.shell.run(command, input=self.resolved_config)

  def deploy(self) -> None:
    self.shell.run(
      command=f'docker stack deploy --prune --detach=true --resolve-image=always -c - {self.driver.stack_id}',
      input=self.resolved_config,
    )

  def ps(self) -> None:
    self.shell.run(f'docker stack ps --no-trunc {self.driver.stack_id}', stderr=False)
    self.shell.run(f'docker stack services {self.driver.stack_id}', stderr=False)

  def logs(self) -> None:
    for service in self.config.get('services', {}).keys():
      self.shell.run(f'docker service logs -n 10 {self.driver.stack_id}_{service}', stderr=False)

  def down(self) -> None:
    self.shell.run(f'docker stack rm {self.driver.stack_id}')
