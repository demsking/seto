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
import functools
import json
import socket
from typing import Any

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
    return self.driver.host

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
    self.localshell.run(
      command=f'docker compose -f - -p {self.driver.stack_id} build --no-cache',
      input=self.resolved_config,
    )

  def push(self) -> None:
    self.localshell.run(
      command=f'docker compose -f - -p {self.driver.stack_id} push',
      input=self.resolved_config,
    )

  def pull(self) -> None:
    self.localshell.run(
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
  def placement_hostnames(self) -> list[str] | None:
    return self.config.get('x-placement-hostnames', None)

  @property
  def placement_hostname(self) -> str | None:
    return self.config.get('x-placement-hostname', None)

  @property
  def placement(self) -> str | list[str]:
    return self.config.get('x-placement', '')

  @property
  @functools.lru_cache(maxsize=128)
  def nodenames(self) -> list[str]:
    specified = []
    if self.placement_hostnames is not None:
      specified.append('x-placement-hostnames')
    if self.placement_hostname:
      specified.append('x-placement-hostname')
    if self.placement:
      specified.append('x-placement')

    if len(specified) > 1:
      raise ValueError(
        f"Only one of 'x-placement-hostnames', 'x-placement-hostname', or 'x-placement' can be specified. Got: {', '.join(specified)}"
      )

    # 1. Parse x-placement-hostnames / x-placement-hostname
    hosts = []
    if self.placement_hostnames is not None:
      if not isinstance(self.placement_hostnames, list):
        raise ValueError('x-placement-hostnames must be a list of strings')
      for h in self.placement_hostnames:
        if not isinstance(h, str):
          raise ValueError('x-placement-hostnames must be a list of strings')
        hosts.append(h.strip())
    elif self.placement_hostname:
      host = str(self.placement_hostname).strip()
      if host:
        hosts.append(host)

    if hosts:
      return list(dict.fromkeys(hosts))

    # 2. Parse x-placement
    placements = []
    if self.placement:
      if isinstance(self.placement, list):
        for p in self.placement:
          if p:
            placements.append(p)
      else:
        placements.extend(self._parse_placement_string(self.placement))

    if not placements:
      raise ValueError('Missing required x-placement or x-placement-hostname field')

    nodes = self.client.nodes.list()
    matched_nodes = []

    for placement in placements:
      if not placement or '==' not in placement:
        continue
      label_key, label_value = placement.split('==', 1)
      label_key = label_key.strip()
      value = label_value.strip()

      for node in nodes:
        if node.attrs and node.attrs['Spec']['Labels'].get(label_key) == value:
          hostname = node.attrs['Description']['Hostname']
          if hostname not in matched_nodes:
            matched_nodes.append(hostname)

    if not matched_nodes:
      raise ValueError(f'Unable to found node for placement "{self.placement}"')

    return matched_nodes

  @property
  def nodename(self) -> str:
    names = self.nodenames
    if not names:
      raise ValueError('No target nodes found')
    return names[0]

  def _parse_hostname_string(self, value: Any) -> list[str]:
    if not value:
      return []
    raw = str(value).strip()
    if ',' in raw:
      return [h.strip() for h in raw.split(',') if h.strip()]
    elif ' ' in raw:
      return [h.strip() for h in raw.split() if h.strip()]
    else:
      return [raw]

  def _parse_placement_string(self, value: Any) -> list[str]:
    if not value:
      return []
    raw = str(value).strip()
    if ',' in raw:
      return [p.strip() for p in raw.split(',') if p.strip()]
    return [raw]

  def _remote_node_run(self, command: str, *, hostname: str, input: str) -> None:
    setting = Setting.from_connection_string(f'{Driver.setouser}@{hostname}')
    node = Driver.get_shell(setting, self.shell.key_file_path)

    node.connect()
    node.run(command, input=input)
    node.close()

  def _exec(self, command: str, nodename: str) -> None:
    if self.hostname == nodename:
      self.shell.run(command=command, input=self.resolved_config)
    else:
      self._remote_node_run(
        command,
        hostname=nodename,
        input=self.resolved_config,
      )

  def info(self) -> None:
    if self.driver.manager:
      command = f'docker compose -p {self.driver.stack_id} -f - config'
    else:
      command = f'docker compose -p {self.driver.stack_id} -f - config --no-env-resolution --skip-interpolation'

    for nodename in self.nodenames:
      self._exec(command, nodename)

  def deploy(self) -> None:
    for nodename in self.nodenames:
      print(f'Deploying on {nodename}...')
      self._exec(
        f'docker compose -p {self.driver.stack_id} -f - up -d --remove-orphans',
        nodename,
      )

  def ps(self) -> None:
    for nodename in self.nodenames:
      print(f'--- Services on {nodename} ---')
      self._exec(f'docker compose -p {self.driver.stack_id} -f - ps', nodename)

  def logs(self) -> None:
    for nodename in self.nodenames:
      print(f'--- Logs on {nodename} ---')
      self._exec(f'docker compose -p {self.driver.stack_id} -f - logs -n 10', nodename)

  def down(self) -> None:
    for nodename in self.nodenames:
      print(f'Removing stack from {nodename}...')
      self._exec(f'docker compose -p {self.driver.stack_id} -f - down', nodename)


class DockerSwarm(Docker):
  @property
  def shell(self) -> Shell:
    return Driver.get_shell(
      Setting.from_connection_string(self.hostname),
    )

  def info(self) -> None:
    if self.driver.manager:
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
