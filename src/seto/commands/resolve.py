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
from collections.abc import Callable

import yaml

from ..core.driver import Driver
from ..core.network import resolve_networks
from ..core.parser import parse_services
from ..core.volume import Volume


def filter_unused_networks(compose_data: dict, config_networks: dict) -> None:
  """Filter compose networks entry to explicitly services's defining networks."""
  used_networks = set()
  services = compose_data.get('services', {})

  if isinstance(services, dict):
    for service_def in services.values():
      if isinstance(service_def, dict):
        service_networks = service_def.get('networks')

        if isinstance(service_networks, list):
          for net in service_networks:
            if isinstance(net, str):
              used_networks.add(net)
        elif isinstance(service_networks, dict):
          for net in service_networks.keys():
            if isinstance(net, str):
              used_networks.add(net)

  networks = compose_data.get('networks') or {}
  filtered_networks = {}

  for net in used_networks:
    if net in networks:
      filtered_networks[net] = networks[net]
    elif net in config_networks:
      filtered_networks[net] = config_networks[net]
    else:
      filtered_networks[net] = {}

  compose_data['networks'] = filtered_networks


def resolve(
  args,
  driver: Driver,
  *,
  inject: bool = False,
  execute: Callable[[dict, list], None] | None = None,
  skip_volumes: bool = False,
  skip_interpolation: bool = False,
) -> dict:
  config_networks = {}
  global_networks = resolve_networks(args.namespace)

  for network_name, network_definition in global_networks.items():
    config_networks[network_name] = {
      'name': network_definition['name'],
      'external': True,
    }

  compose = {
    'x-placement': [],
    'configs': {},
    'networks': {},
    'volumes': {},
    'secrets': {},
    'services': {},
  }

  def parse(resolved_compose_data: dict, volumes: list[Volume]):
    is_compose_mode = resolved_compose_data.get('x-mode', 'swarm') == 'compose'
    placement: list | None = (
      resolved_compose_data.get('x-placement', None) if is_compose_mode else None
    )

    networks_ = resolved_compose_data.get('networks', {})
    services = resolved_compose_data.get('services', {})
    volumes = resolved_compose_data.get('volumes', {})
    configs = resolved_compose_data.get('configs', {})
    secrets = resolved_compose_data.get('secrets', {})

    # In compose mode, fall back to service-level Swarm placement constraints
    # when no top-level x-placement is specified.
    if is_compose_mode and not placement:
      file_service_constraints = []

      for service_name, service_def in services.items():
        constraints = service_def.get('deploy', {}).get('placement', {}).get('constraints', [])

        if isinstance(constraints, list) and constraints:
          cleaned = [c.strip() for c in constraints if isinstance(c, str) and '==' in c]

          if cleaned:
            file_service_constraints.append(cleaned)
      if file_service_constraints:
        resolved_compose_data['x-placement'] = file_service_constraints
        placement = file_service_constraints

    resolved_compose_data['networks'] = {**config_networks, **networks_}
    filter_unused_networks(resolved_compose_data, config_networks)

    # Aggregate x-placement
    if is_compose_mode and placement:
      if isinstance(placement, list) and any(isinstance(i, list) for i in placement):
        # Nested list of constraint groups
        for group in placement:
          if isinstance(group, list):
            cleaned_group = [c.strip() for c in group if isinstance(c, str) and '==' in c]
            if cleaned_group and cleaned_group not in compose['x-placement']:
              compose['x-placement'].append(cleaned_group)
          elif isinstance(group, str):
            part = group.strip()
            if part and '==' in part:
              wrapped = [part]
              if wrapped not in compose['x-placement']:
                compose['x-placement'].append(wrapped)
      else:
        # Flat placement (single string or list of strings)
        raw_placements = []
        if isinstance(placement, list):
          for item in placement:
            if isinstance(item, str):
              raw_placements.extend([p.strip() for p in item.split(',') if p.strip()])
        else:
          raw_placements.extend([p.strip() for p in str(placement).split(',') if p.strip()])

        for part in raw_placements:
          if part and part not in compose['x-placement']:
            compose['x-placement'].append(part)

    compose['networks'].update(resolved_compose_data['networks'])
    compose['services'].update(services)
    compose['volumes'].update(volumes)
    compose['configs'].update(configs)
    compose['secrets'].update(secrets)

    if execute and is_compose_mode:
      execute(resolved_compose_data, placement or [])

  stack = args.stack or args.namespace

  parse_services(
    driver=driver,
    image_prefix=args.image_prefix,
    stack=stack,
    default_volume_mount_mode=args.default_volume_mount_mode,
    default_nfs_options=args.default_nfs_options,
    execute=parse,
    inject=inject,
    skip_interpolation=skip_interpolation,
    skip_volumes=skip_volumes,
  )

  if not compose['x-placement']:
    compose['x-placement'] = None

  return compose


def execute_resolve_command(args, driver: Driver) -> None:
  compose = resolve(args, driver, skip_interpolation=getattr(args, 'skip_interpolation', False))
  cleaned_compose = {k: v for k, v in compose.items() if v}
  compose_output = yaml.dump(cleaned_compose, default_flow_style=False, sort_keys=False) or '{}'
  print(compose_output.strip())
