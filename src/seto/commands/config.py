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
import re
from collections.abc import Callable

import yaml

from ..core.driver import Driver
from ..core.network import get_global_external_networks
from ..core.network import resolve_networks
from ..core.parser import parse_services
from ..core.volume import Volume


def resolve(
  args,
  driver: Driver,
  *,
  inject: bool = False,
  execute: Callable[[dict, str | list[str]], None] | None = None,
) -> dict:
  config_networks = get_global_external_networks()
  global_networks = resolve_networks(args.project)

  for network_name, network_definition in global_networks.items():
    config_networks[network_name] = {
      'name': network_definition['name'],
      'external': True,
    }

  compose = {
    'x-placement': [],
    'x-placement-hostnames': [],
    'x-placement-hostname': None,
    'configs': {},
    'networks': config_networks,
    'volumes': {},
    'secrets': {},
    'services': {},
  }

  def parse(resolved_compose_data: dict, volumes: list[Volume]):
    placement_hostnames: list[str] | None = resolved_compose_data.get('x-placement-hostnames', None)
    placement_hostname: str | None = resolved_compose_data.get('x-placement-hostname', None)
    placement: str | list[str] | None = resolved_compose_data.get('x-placement', None)

    specified = []
    if placement_hostnames is not None:
      specified.append('x-placement-hostnames')
    if placement_hostname:
      specified.append('x-placement-hostname')
    if placement:
      specified.append('x-placement')

    if len(specified) > 1:
      raise ValueError(
        f"Only one of 'x-placement-hostnames', 'x-placement-hostname', or 'x-placement' can be specified. Got: {', '.join(specified)}"
      )

    networks_ = resolved_compose_data.get('networks', {})
    services = resolved_compose_data.get('services', {})
    volumes = resolved_compose_data.get('volumes', {})
    configs = resolved_compose_data.get('configs', {})
    secrets = resolved_compose_data.get('secrets', {})

    resolved_compose_data['networks'] = {**config_networks, **networks_}

    # Aggregate x-placement-hostnames & x-placement-hostname
    if placement_hostnames is not None:
      if not isinstance(placement_hostnames, list):
        raise ValueError('x-placement-hostnames must be a list of strings')
      for h in placement_hostnames:
        if not isinstance(h, str):
          raise ValueError('x-placement-hostnames must be a list of strings')
        part = h.strip()
        if part and part not in compose['x-placement-hostnames']:
          compose['x-placement-hostnames'].append(part)

    if placement_hostname:
      host = str(placement_hostname).strip()
      if host:
        if host not in compose['x-placement-hostnames']:
          compose['x-placement-hostnames'].append(host)
        if not compose['x-placement-hostname']:
          compose['x-placement-hostname'] = host

    # Aggregate x-placement
    if placement:
      def add_placements(val):
        if isinstance(val, list):
          for item in val:
            add_placements(item)
        elif val:
          for part in re.split(r'[,\s]+', str(val)):
            if part and part not in compose['x-placement']:
              compose['x-placement'].append(part)

      add_placements(placement)

    compose['networks'].update(networks_)
    compose['services'].update(services)
    compose['volumes'].update(volumes)
    compose['configs'].update(configs)
    compose['secrets'].update(secrets)

    if args.compose and not placement and not placement_hostname and not placement_hostnames:
      raise ValueError('Missing required x-placement, x-placement-hostnames or x-placement-hostname field')

    if execute:
      execute(resolved_compose_data, placement_hostnames or placement_hostname or placement or '')

  parse_services(
    driver=driver,
    image_prefix=args.image_prefix,
    stack=args.stack or args.project,
    default_volume_mount_mode=args.default_volume_mount_mode,
    default_nfs_options=args.default_nfs_options,
    execute=parse,
    inject=inject,
    mode=['compose'] if args.compose else ['swarm'],
  )

  # Simplify arrays to a single string if they contain only one element
  if len(compose['x-placement-hostnames']) == 1:
    compose['x-placement-hostnames'] = compose['x-placement-hostnames'][0]
  elif not compose['x-placement-hostnames']:
    compose['x-placement-hostnames'] = None

  if len(compose['x-placement']) == 1:
    compose['x-placement'] = compose['x-placement'][0]
  elif not compose['x-placement']:
    compose['x-placement'] = None

  return compose


def execute_config_command(args, driver: Driver) -> None:
  compose = resolve(args, driver)
  compose_output = yaml.dump(compose) or '{}'

  if args.compose:
    command = f'docker compose -p {driver.stack_id} -f - config'
  else:
    command = 'docker stack config -c -'

  driver.shell.run(command, input=str(compose_output))
