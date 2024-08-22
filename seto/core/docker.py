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
import functools
from typing import Any

import yaml
from docker import DockerClient

from .driver import Driver
from .parser import resolve_env_vars
from .shell import Shell


class Docker:
  def __init__(
    self,
    stack: str,
    config: dict,
    driver: Driver,
    client: DockerClient,
  ) -> None:
    self.stack = stack
    self.driver = driver
    self.client = client
    self.config = config

  @property
  def shell(self) -> Shell:
    return self.driver.shell

  @property
  def resolved_config(self) -> str:
    return resolve_env_vars(yaml.dump(self.config))

  @property
  def external_networks(self) -> list[str]:
    return [
      item.attrs['Name'] for item in self.client.networks.list()
      if item.attrs['Name'].startswith(self.stack)
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
      command=f'docker compose -f - -p {self.stack} build --quiet',
      pipe_input=self.resolved_config,
    )

  def push(self) -> None:
    Shell.pipe_exec(
      command=f'docker compose -f - -p {self.stack} push --quiet',
      pipe_input=self.resolved_config,
    )

  def pull(self) -> None:
    Shell.pipe_exec(
      command=f'docker compose -f - -p {self.stack} pull --quiet --policy=always',
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
  def placement(self) -> dict[str, Any]:
    return self.config['x-placement']

  @property
  @functools.lru_cache(maxsize=128)
  def node_hostname(self) -> str | None:
    nodes = self.client.nodes.list()

    label_key, label_value = self.placement.split('==')
    value = f'{label_value}'

    for node in nodes:
      if node.attrs['Spec']['Labels'].get(label_key) == value:
        return node.attrs['Description']['Hostname']

    return None

  def info(self) -> None:
    self._exec(f'docker compose -p {self.stack} -f - config')

  def deploy(self) -> None:
    print(f'Deploying on {self.node_hostname}...')
    self._exec(f'docker compose -p {self.stack} -f - up -d --remove-orphans')

  def ps(self) -> None:
    self._exec(f'docker compose -p {self.stack} -f - ps')

  def logs(self) -> None:
    self._exec(f'docker compose -p {self.stack} -f - logs')

  def down(self) -> None:
    self._exec(f'docker compose -p {self.stack} -f - down')

  def _exec(self, command: str) -> None:
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
      command=f'docker stack deploy --prune --detach=true --resolve-image=always -c - {self.stack}',
      pipe_input=self.resolved_config,
    )

  def ps(self) -> None:
    # Shell.exec(f'docker stack ps --no-trunc - {self.stack}')
    Shell.exec(f'docker stack services {self.stack}')

  def logs(self) -> None:
    # Shell.exec(f'docker stack service logs -f -c - {self.stack}')
    pass

  def down(self) -> None:
    Shell.exec(f'docker stack rm {self.stack}')
