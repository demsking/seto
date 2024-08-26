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
import json
import os
import re
from typing import Any

from docker import DockerClient

from ..core.dns import resolve_hostname
from ..core.docker import DockerCompose
from ..core.docker import DockerSwarm
from ..core.driver import Driver
from ..core.parser import resolve_networks
from .config import resolve

# Define the regular expression pattern to match {{ .Node.Hostname }} with optional spaces
NODE_HOSTNAME_RE = r'\{\{\s*\.Node\.Hostname\s*\}\}'


def parse_service_vars(entries: dict[str, Any], hostname: str) -> None:
  for key, value in entries.items():
    if isinstance(value, str):
      entries[key] = re.sub(NODE_HOSTNAME_RE, hostname, value)


def get_label_value(labels: dict[str, Any], name: str) -> Any | None:
  for label, value in labels.items():
    if label.endswith(name):
      return value
  return None


def parse_compose_config(
  stack: str,
  args,
  driver: Driver,
  client: DockerClient,
  networks_list: list[str],
  swarm_config: dict,
  *,
  compose_config: dict,
  placement: str,
  composes: list[DockerCompose],
  traefik_http_provider_routers: dict,
  traefik_http_provider_services: dict,
) -> None:
  if compose_config['services']:
    compose = DockerCompose(
      stack=stack,
      client=client,
      driver=driver,
      config=compose_config,
    )

    composes.append(compose)

    for service_name, service in compose_config['services'].items():
      service_bridge_name = f'{service_name}_bridge'
      service_environment = service.get('environment', {})
      service_labels = service.get('labels', {})
      service_networks = service.get('networks', [])
      service_ports = service.get('ports', [])
      service_deploy = {
        'labels': [
          'traefik.discovery.enable=false',
        ],
        'placement': {
          'constraints': [
            f'node.labels.{placement}',
          ],
        },
      }

      # Use a shadow service to make sure all networks are created on all node
      swarm_config['services'][service_bridge_name] = {
        'image': 'traefik/whoami',
        'networks': service_networks,
        'deploy': service_deploy,
      }

      parse_service_vars(service_labels, compose.node_hostname)
      parse_service_vars(service_environment, compose.node_hostname)

      if service_ports:
        service_traefik_rule = get_label_value(service_labels, '.rule')
        service_traefik_middlewares = get_label_value(service_labels, '.middlewares')
        service_traefik_port = get_label_value(service_labels, '.loadbalancer.server.port')
        service_traefik_entryPoints = get_label_value(service_labels, '.entryPoints')
        service_traefik_tls_certresolver = get_label_value(service_labels, '.tls.certresolver')
        service_traefik_service = get_label_value(service_labels, '.service')

        if not service_traefik_port:
          raise ValueError(f'Service "{service_name}" error: port is missing')

        published_port = -1

        for entry in service_ports:
          source_port, target_port = entry.split(':')

          if int(target_port) == service_traefik_port:
            published_port = source_port

        if published_port == -1:
          raise ValueError(f'No exposed port for {service_name}:{service_traefik_port}')

        traefik_http_provider_routers[service_name] = {
          'entryPoints': [service_traefik_entryPoints],
          'service': service_traefik_service or service_name,
          'rule': service_traefik_rule,
          'middlewares': service_traefik_middlewares,
          'tls': {
            'certresolver': service_traefik_tls_certresolver,
          },
        }

        node_ip = resolve_hostname(compose.node_hostname)
        traefik_http_provider_services[service_name] = {
          'loadBalancer': {
            'servers': [
              {
                'url': f'http://{node_ip}:{published_port}',
              },
            ],
          },
        }


def deploy_seto_stack(args, driver: Driver) -> None:
  client = DockerClient.from_env()
  config_networks = resolve_networks(args.project)

  config_networks.update({
    'cloud-public': {
      'name': 'seto-cloud-public',
      'driver': 'overlay',
      'attachable': True,
    },
    'cloud-edge': {
      'name': 'seto-cloud-edge',
      'driver': 'overlay',
      'attachable': True,
    },
  })

  print('Configuring ṣeto agents...')
  internal_stack = {
    'networks': config_networks,
    'services': {
      'agent': {
        'image': 'traefik/whoami',
        'networks': list(config_networks.keys()),
        'deploy': {'mode': 'global'},
      },
    },
  }

  swarm = DockerSwarm(
    stack='seto',
    client=client,
    driver=driver,
    config=internal_stack,
  )

  swarm.info()
  swarm.deploy()


def execute_deploy_command(args, driver: Driver) -> None:
  client = DockerClient.from_env()

  # Docker Swarm
  print(f'Resolving {driver.stack} services...')
  setattr(args, 'compose', False)
  swarm_config = resolve(args, driver)

  swarm = DockerSwarm(
    stack=driver.stack,
    client=client,
    driver=driver,
    config=swarm_config,
  )

  # Docker Compose
  setattr(args, 'compose', True)
  networks_list = list(swarm_config['networks'].keys())
  composes_items: list[DockerCompose] = []

  bridges_path = 'bridges'
  traefik_http_provider_name = 'traefik-http-provider'
  traefik_http_provider_filename = os.path.join(bridges_path, 'traefik-http-provider.json')
  traefik_http_provider_routers = {}
  traefik_http_provider_services = {}
  traefik_http_provider = {
    'http': {
      'routers': traefik_http_provider_routers,
      'services': traefik_http_provider_services,
    },
  }

  swarm_config['configs'][traefik_http_provider_name] = {
    'file': traefik_http_provider_filename,
  }

  swarm_config['services']['compose-provider'] = {
    'image': 'httpd:alpine',
    'networks': networks_list,
    'configs': [
      {
        'source': traefik_http_provider_name,
        'target': '/usr/local/apache2/htdocs/bridge.json',
      },
    ],
    'deploy': {
      'labels': [
        'traefik.discovery.enable=false',
      ],
    },
  }

  resolve(
    args,
    driver,
    inject=True,
    execute=lambda config, placement: parse_compose_config(
      driver.stack,
      args,
      driver,
      client,
      networks_list,
      swarm_config,
      compose_config=config,
      placement=placement,
      composes=composes_items,
      traefik_http_provider_routers=traefik_http_provider_routers,
      traefik_http_provider_services=traefik_http_provider_services,
    ),
  )

  if not os.path.exists(bridges_path):
    os.mkdir(bridges_path)

  with open(traefik_http_provider_filename, 'w', encoding='utf-8') as file:
    file.write(json.dumps(traefik_http_provider, indent='  '))

  print(f'Building {driver.stack} swarm images...')
  # swarm.info()
  swarm.build()
  swarm.push()

  print(f'Deploying {driver.stack} swarm environment...')
  swarm.deploy()
  swarm.ps()

  if composes_items:
    print(f'Building {driver.stack} compose images...')
    for compose in composes_items:
      # compose.info()
      compose.build()
      compose.push()

    print(f'Pulling {driver.stack} compose images...')
    for compose in composes_items:
      compose.pull()

    print(f'Deploying {driver.stack} compose environment...')
    for compose in composes_items:
      compose.deploy()
      compose.ps()
      compose.logs()
