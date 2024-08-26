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
import glob
import os
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any
from typing import Literal
from typing import TypedDict

import yaml

from .driver import Driver
from .permissions import parse_permission_mode
from .volume import Volume

ResolveMode = Literal['compose', 'swarm']
Service = dict[str, Any]


class TopLevelConfig(TypedDict):
  file: str


class ServiceConfig(TypedDict):
  source: str
  target: str
  mode: int


def parse_stack_values(entry: dict | list) -> None:
  if isinstance(entry, dict):
    for key, value in entry.items():
      if isinstance(value, bool):
        entry[key] = 'true' if value else 'false'


def parse_volume_entry(entry: str, default_mode='rw') -> tuple[str, str, str]:
  source, target, mode = (entry.split(':') + [default_mode])[0:3]

  return source, target, mode


def parse_local_volumes(
  stack: str,
  *,
  service_name: str,
  service: Service,
) -> None:
  local_volumes = []

  if 'volumes-image' in service:
    service_volumes_image = service.get('volumes-image', [])

    del service['volumes-image']

    for volume_entry in service_volumes_image:
      if isinstance(volume_entry, str):
        if volume_entry.startswith('./'):
          source, target, _ = parse_volume_entry(volume_entry)
          local_volumes.append((source, target))

  if len(local_volumes) > 0:
    _, image_version = tuple((service['image'].split(':') + ['latest'])[0:2])

    image_name = f'{stack}-{service_name}'.replace('_', '-')
    service_dockerfile_name = f'{image_name}.dockerfile'
    service_dockerfile_file = os.path.join('images', service_dockerfile_name)
    service_dockerfile_definition = [
      f'FROM {service["image"]}',
    ] + [f'COPY {source} {target}' for source, target in local_volumes] + ['']
    service_dockerfile = '\n'.join(service_dockerfile_definition)
    service_dockerfile = resolve_env_vars(service_dockerfile)

    service['image'] = f'demsking/{image_name}:{image_version}'
    service['build'] = {
      'context': '.',
      'dockerfile': service_dockerfile_file,
    }

    with open(service_dockerfile_file, 'w', encoding='utf-8') as dockerfile:
      dockerfile.write(service_dockerfile)


def parse_volumes(
  driver: Driver,
  service_name: str,
  service: Service,
  *,
  compose_volumes: dict,
  volumes: list[Volume],
) -> None:
  if 'volumes-nfs' in service:
    service_volumes = service.get('volumes', [])
    service_volumes_nfs = service.get('volumes-nfs', [])
    service['volumes'] = service_volumes

    del service['volumes-nfs']

    for volume_entry in service_volumes_nfs:
      source, target, volume_mode = parse_volume_entry(volume_entry)
      target_folder = target

      if volume_entry.startswith('~/') or volume_entry.startswith('./'):
        device_source = os.path.expanduser(source)

        if os.path.isfile(device_source):
          target_folder = os.path.dirname(target)

        volume_name = source[2:].replace('/', '-').replace('.', '')
      else:
        device_source = None
        volume_name = source

        if volume_name in compose_volumes:
          del compose_volumes[volume_name]

      if volume_mode == 'norename':
        volume_mode = 'rw'
      elif service_name not in volume_name:
        volume_name = f'{volume_name}-{service_name}'

      if device_source and os.path.isfile(device_source):
        filename = os.path.basename(target)
        target = os.path.join(volume_name, filename)
      else:
        target = volume_name

      volume = Volume(
        name=volume_name.replace('-', '_'),
        source=device_source,
        target=target,
        mount_folder=volume_name,
      )

      compose_volumes[volume.name] = driver.resolve_compose_volume(volume)

      service_volumes.append(f'{volume.name}:{target_folder}:{volume_mode}')
      volumes.append(volume)


def resolve_compose_file(
  driver: Driver,
  *,
  compose_dir: str,
  compose_data: dict,
  inject: bool = False,
  mode: list[ResolveMode] | None = None,
) -> tuple[dict, list]:
  mode_value = mode or ['swarm']
  updated_compose_data = compose_data.copy()
  compose_services = updated_compose_data.get('services', {})
  compose_volumes = updated_compose_data.get('volumes', {})
  compose_configs: dict[str, TopLevelConfig] = updated_compose_data.get('configs', {})
  volumes: list[Volume] = []

  for service_name, service in compose_services.items():
    parse_service_configs(
      service_name=service_name,
      service=service,
      configs=compose_configs,
      inject=inject,
    )

    service_environment = service.get('environment', {})
    service_deploy = service.get('deploy', {})
    service_deploy_labels = service_deploy.get('labels', {})
    service_labels = service.get('labels', {})

    service_deploy_labels.update(service_labels)
    parse_stack_values(service_deploy_labels)
    parse_local_volumes(driver.stack, service_name=service_name, service=service)

    parse_volumes(
      driver=driver,
      service_name=service_name,
      service=service,
      compose_volumes=compose_volumes,
      volumes=volumes,
    )

    if 'compose' in mode_value:
      if 'labels' in service_deploy:
        del service_deploy['labels']

      service['labels'] = service_deploy_labels

    if 'swarm' in mode_value:
      parse_stack_values(service_environment)

      if 'hostname' not in service:
        service['hostname'] = '{{.Node.Hostname}}'

      if 'labels' in service:
        del service['labels']

      service['deploy'] = service_deploy
      service_deploy['labels'] = service_deploy_labels

    if 'command' in service and service['command'] is None:
      del service['command']

  updated_compose_data['configs'] = compose_configs
  updated_compose_data['volumes'] = compose_volumes

  return updated_compose_data, volumes


def parse_service_configs(
  service_name: str,
  service: Service,
  *,
  configs: dict[str, TopLevelConfig],
  inject: bool = False,
) -> None:
  new_volumes: list[str] = []
  service_volumes: list[str] = service.get('volumes', [])
  service_configs: list[ServiceConfig] = service.get('configs', [])

  for volume in service_volumes:
    if ':' in volume:
      source, target, mode = parse_volume_entry(volume, 'r')

      if source.startswith('./') or source.startswith('../'):
        if os.path.isfile(source):
          config_name = re.sub(r'_{2,}', '_', f"{service_name}_{source.replace('/', '_').replace('.', '_')}".replace('-', '_'))

          if inject:
            with open(source, encoding='utf-8') as config:
              configs[config_name] = {
                'content': config.read(),
              }
          else:
            configs[config_name] = {
              'file': source,
            }

          service_configs.append({
            'source': config_name,
            'target': target,
            'mode': parse_permission_mode(mode),
          })

          continue

    new_volumes.append(volume)

  if len(new_volumes) != len(service_volumes):
    service['configs'] = service_configs
    service['volumes'] = new_volumes


def resolve_env_vars(content: str) -> str:
  output = subprocess.run(
    ['envsubst'],
    input=content,
    text=True,
    capture_output=True,
    check=True,
  )

  return output.stdout


def parse_compose_file(compose_file: str, resolve_vars=False) -> tuple[dict, str, str]:
  compose_file = os.path.realpath(compose_file)
  compose_dir = os.path.dirname(compose_file)

  with open(compose_file, encoding='utf-8') as file:
    compose_content = file.read()

    if resolve_vars:
      compose_content = resolve_env_vars(compose_content)

    compose_data = yaml.safe_load(compose_content)

  return compose_data, compose_file, compose_dir


def resolve_networks(
  project: str,
  external_networks: list[str] | None = None,
) -> dict:
  networks_files = glob.glob(os.path.join('networks', '*.yaml'))
  merged_data = {}

  for network_file in networks_files:
    with open(network_file, encoding='utf-8') as file:
      network_key = os.path.splitext(os.path.basename(network_file))[0]
      network_data = yaml.safe_load(file)
      network_name = network_data.get('name', f'{project}_{network_key}')
      merged_data[network_key] = network_data

      if external_networks and network_key in external_networks:
        network_data.clear()

        network_data['external'] = True

      network_data['name'] = network_name

  return merged_data


def parse_services(
  driver: Driver,
  stack: str,
  *,
  execute: Callable[[dict, list], None] | None = None,
  mode: list[ResolveMode] | None = None,
  inject: bool = False,
) -> tuple[dict, list]:
  services_files = glob.glob(os.path.join(stack, '*.yaml'))
  output_resolved_compose_data = []
  output_volumes = []

  for service_file in services_files:
    resolve_vars = mode and 'compose' in mode
    compose_data, _, compose_dir = parse_compose_file(service_file, resolve_vars)
    x_mode = compose_data.get('x-mode', 'swarm')

    if x_mode not in ['compose', 'swarm']:
      print('x-mode must be either "compose" or "warm"')
      sys.exit(42)

    if mode:
      if 'compose' in mode and 'compose' != x_mode:
        continue

      if 'swarm' in mode and 'swarm' != x_mode:
        continue

    # resolve compose local volumes
    resolved_compose_data, volumes = resolve_compose_file(
      driver=driver,
      compose_dir=compose_dir,
      compose_data=compose_data,
      inject=inject,
      mode=[x_mode],
    )

    output_resolved_compose_data.append(resolved_compose_data)
    output_volumes.append(volumes)

    if execute:
      execute(resolved_compose_data, volumes)

  return output_resolved_compose_data, output_volumes
