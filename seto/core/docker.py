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

  @property
  def shell(self) -> Shell:
    return self.driver.shell

  @property
  def resolved_config(self) -> str:
    return resolve_env_vars(json.dumps(self.config))

  @property
  def external_networks(self) -> list[str]:
    return [
      item.attrs['Name'] for item in self.client.networks.list()
      if item.attrs['Name'].startswith(self.driver.stack_id)
    ]

  @staticmethod
  def remote_node_run(
    command: str,
    *,
    hostname: str,
    pipe_input: str,
  ) -> None:
    ssh_command = f"ssh {Driver.setouser}@{hostname} '{command}'"
    return Shell.pipe_exec(ssh_command, pipe_input=pipe_input)

  def build(self) -> None:
    Shell.pipe_exec(
      command=f'docker compose -f - -p {self.driver.stack_id} build --no-cache',
      pipe_input=self.resolved_config,
    )

  def push(self) -> None:
    Shell.pipe_exec(
      command=f'docker compose -f - -p {self.driver.stack_id} push',
      pipe_input=self.resolved_config,
    )

  def pull(self) -> None:
    Shell.pipe_exec(
      command=f'docker compose -f - -p {self.driver.stack_id} pull --policy=always',
      pipe_input=self.resolved_config,
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
  def placement(self) -> str | None:
    return self.config.get('x-placement', None)

  @property
  def current_hostname(self) -> str:
    return socket.gethostname()

  @property
  @functools.lru_cache(maxsize=128)
  def node_hostname(self) -> str | None:
    if self.placement_hostname:
      return self.placement_hostname

    nodes = self.client.nodes.list()

    label_key, label_value = self.placement.split('==')
    label_key = f'{label_key}'.strip()
    value = f'{label_value}'.strip()

    for node in nodes:
      if node.attrs['Spec']['Labels'].get(label_key) == value:
        return node.attrs['Description']['Hostname']

    raise ValueError(f'Unable to found node for placement "{self.placement}"')

  def info(self) -> None:
    self._exec(f'docker compose -p {self.driver.stack_id} -f - config')

  def deploy(self) -> None:
    print(f'Deploying on {self.node_hostname}...')
    self._exec(f'docker compose -p {self.driver.stack_id} -f - up -d --remove-orphans')

  def ps(self) -> None:
    self._exec(f'docker compose -p {self.driver.stack_id} -f - ps')

  def logs(self) -> None:
    self._exec(f'docker compose -p {self.driver.stack_id} -f - logs')

  def down(self) -> None:
    self._exec(f'docker compose -p {self.driver.stack_id} -f - down')

  def _exec(self, command: str) -> None:
    if self.current_hostname == self.node_hostname:
      Shell.pipe_exec(command=command, pipe_input=self.resolved_config)
    else:
      Docker.remote_node_run(
        command,
        hostname=self.node_hostname,
        pipe_input=self.resolved_config,
      )


class DockerSwarm(Docker):
  def info(self) -> None:
    Shell.pipe_exec(
      command='docker stack config -c -',
      pipe_input=self.resolved_config,
    )

  def deploy(self) -> None:
    Shell.pipe_exec(
      command=f'docker stack deploy --prune --detach=true --resolve-image=always -c - {self.driver.stack_id}',
      pipe_input=self.resolved_config,
    )

  def ps(self) -> None:
    # Shell.exec(f'docker stack ps --no-trunc - {self.driver.stack_id}')
    Shell.exec(f'docker stack services {self.driver.stack_id}')

  def logs(self) -> None:
    # Shell.exec(f'docker stack service logs -f -c - {self.driver.stack_id}')
    pass

  def down(self) -> None:
    Shell.exec(f'docker stack rm {self.driver.stack_id}')
