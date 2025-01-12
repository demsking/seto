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
from collections.abc import Callable

import yaml

from ..core.driver import Driver
from ..core.network import get_global_external_networks
from ..core.network import resolve_networks
from ..core.parser import parse_services
from ..core.shell import Shell
from ..core.volume import Volume


def resolve(
  args,
  driver: Driver,
  *,
  inject: bool = False,
  execute: Callable[[dict, str], None] | None = None,
) -> dict:
  config_networks = get_global_external_networks()
  global_networks = resolve_networks(args.project)

  for network_name, network_definition in global_networks.items():
    config_networks[network_name] = {
      'name': network_definition['name'],
      'external': True,
    }

  compose = {
    'x-placement': None,
    'x-placement-hostname': None,
    'configs': {},
    'networks': config_networks,
    'volumes': {},
    'secrets': {},
    'services': {},
  }

  def parse(resolved_compose_data: dict, volumes: list[Volume]):
    placement_hostname = resolved_compose_data.get('x-placement-hostname', None)
    placement = resolved_compose_data.get('x-placement', None)
    networks_ = resolved_compose_data.get('networks', {})
    services = resolved_compose_data.get('services', {})
    volumes = resolved_compose_data.get('volumes', {})
    configs = resolved_compose_data.get('configs', {})
    secrets = resolved_compose_data.get('secrets', {})

    resolved_compose_data['networks'] = {**config_networks, **networks_}
    compose['x-placement-hostname'] = placement_hostname
    compose['x-placement'] = placement
    compose['networks'].update(networks_)
    compose['services'].update(services)
    compose['volumes'].update(volumes)
    compose['configs'].update(configs)
    compose['secrets'].update(secrets)

    if args.compose and not placement and not placement_hostname:
      raise ValueError('Missing required x-placement or x-placement-hostname field')

    if execute:
      execute(resolved_compose_data, placement_hostname or placement)

  parse_services(
    driver=driver,
    stack=args.stack or args.project,
    execute=parse,
    inject=inject,
    mode=['compose'] if args.compose else ['swarm'],
  )

  return compose


def execute_config_command(args, driver: Driver) -> None:
  compose = resolve(args, driver)
  compose_output = yaml.dump(compose)

  if args.compose:
    command = f'docker compose -p {driver.stack_id} -f - config'
  else:
    command = 'docker stack config -c -'

  Shell.pipe_exec(command, pipe_input=compose_output)
