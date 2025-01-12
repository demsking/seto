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
import json
import os
import random
import re
from typing import Any

from docker import DockerClient

from ..core.dns import resolve_hostname
from ..core.docker import DockerCompose
from ..core.docker import DockerSwarm
from ..core.driver import Driver
from ..core.network import GLOBAL_NETWORKS
from ..core.network import resolve_networks
from ..core.parser import resolve_compose_file
from ..core.shell import Setting
from ..core.traefik import convert_middlewares_to_dict
from .config import resolve


# Define the regular expression pattern to match {{ .Node.Hostname }} with optional spaces
NODE_HOSTNAME_RE = r'\{\{\s*\.Node\.Hostname\s*\}\}'
HTTP_PROVIDER_SERVICENAME = 'seto-http-provider'


def parse_service_vars(entries: dict[str, Any], hostname: str) -> None:
  for key, value in entries.items():
    if isinstance(value, str):
      entries[key] = re.sub(NODE_HOSTNAME_RE, hostname, value)


def pick_label_value(labels: dict[str, Any], name: str) -> Any | str:
  for label, value in labels.items():
    if label.endswith(name):
      del labels[label]
      return value
  return ''


def parse_compose_config(
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
  traefik_http_provider_middlewares: dict,
) -> None:
  if compose_config['services']:
    compose = DockerCompose(
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
        'placement': {
          'constraints': [
            f'node.labels.{placement}' if '=' in placement else f'node.hostname == {placement}',
          ],
        },
      }

      # Use a shadow service to make sure all networks are created on all node
      swarm_config['services'][service_bridge_name] = {
        'image': 'traefik/whoami',
        'networks': service_networks,
        'deploy': service_deploy,
      }

      if compose.node_hostname:
        parse_service_vars(service_labels, compose.node_hostname)
        parse_service_vars(service_environment, compose.node_hostname)

      service['ports'] = service_ports
      service_traefik_rule = pick_label_value(service_labels, '.rule')
      service_traefik_middlewares = pick_label_value(service_labels, '.middlewares')
      service_traefik_port = pick_label_value(service_labels, '.loadbalancer.server.port')
      service_traefik_entryPoints = pick_label_value(service_labels, '.entryPoints')
      service_traefik_tls_certresolver = pick_label_value(service_labels, '.tls.certresolver')
      service_traefik_service = pick_label_value(service_labels, '.service') or service_name
      published_port = random.randint(53100, 64200)

      if not service_traefik_port:
        print(f'WARN: Service "{service_name}" has no defined port. Skipped from Traefik HTTP Provider')
        continue

      service_ports.append(f'{published_port}:{service_traefik_port}')

      traefik_http_provider_routers[service_traefik_service] = {
        'entryPoints': [service_traefik_entryPoints],
        'service': service_traefik_service,
        'rule': service_traefik_rule,
        'middlewares': [item for item in service_traefik_middlewares.split(',') if item],
        'tls': {
          'certresolver': service_traefik_tls_certresolver,
        },
      }

      node_ip = resolve_hostname(compose.node_hostname)
      traefik_http_provider_services[service_traefik_service] = {
        'loadBalancer': {
          'servers': [
            {
              'url': f'http://{node_ip}:{published_port}',
            },
          ],
        },
      }

      traefik_http_provider_middlewares.update(
        convert_middlewares_to_dict(service_labels),
      )


def deploy_seto_stack(args, driver: Driver, replica: list[Setting]) -> None:
  # Temporary rewrite driver config
  driver.project = 'seto'
  driver.stack = None

  # Building seto config
  client = DockerClient.from_env()
  config_networks = resolve_networks(args.project)

  config_networks.update(GLOBAL_NETWORKS)

  print('Configuring seto-http-provider...')
  traefik_http_provider_data_path = '/data'
  internal_stack = {
    'networks': config_networks,
    'services': {
      HTTP_PROVIDER_SERVICENAME: {
        'image': 'demsking/traefik-http-provider',
        'networks': list(config_networks.keys()),
        'environment': [
          'WORKER=1',
          'EXPIRATION_MINUTES=10',
          f'DATA_PATH={traefik_http_provider_data_path}',
        ],
        'volumes-nfs': {
          f'data:{traefik_http_provider_data_path}',
        },
        'deploy': {
          'mode': 'global',
          'labels': {
            'traefik.discovery.enable': True,
          },
        },
      },
    },
  }

  # Resolving compose local volumes
  resolved_compose_data, volumes = resolve_compose_file(
    driver=driver,
    compose_data=internal_stack,
    inject=True,
  )

  print('Creating seto volumes...')
  driver.create_volumes(replica=replica, volumes=volumes, force=args.force)

  print('Deploying seto services...')
  swarm = DockerSwarm(
    client=client,
    driver=driver,
    config=resolved_compose_data,
  )

  swarm.info()
  swarm.deploy()

  # Restore initial driver config
  driver.project = args.project
  driver.stack = args.stack


def execute_deploy_command(args, driver: Driver) -> None:
  client = DockerClient.from_env()

  # Docker Swarm
  print(f'Resolving {driver.stack_id} services...')
  setattr(args, 'compose', False)
  swarm_config = resolve(args, driver)

  swarm = DockerSwarm(
    client=client,
    driver=driver,
    config=swarm_config,
  )

  # Docker Compose
  setattr(args, 'compose', True)
  networks_list = list(swarm_config['networks'].keys())
  composes_items: list[DockerCompose] = []

  bridges_path = 'bridges'
  traefik_http_provider_filename = os.path.join(bridges_path, 'traefik-http-provider.json')
  traefik_http_provider_target = '/traefik/config.json'
  traefik_http_provider_routers = {}
  traefik_http_provider_services = {}
  traefik_http_provider_middlewares = {}
  traefik_http_provider = {
    'http': {
      'routers': traefik_http_provider_routers,
      'services': traefik_http_provider_services,
      'middlewares': traefik_http_provider_middlewares,
    },
  }

  register_command = ' && '.join([
    f'echo "Registering service {driver.stack_id}..."',
    f'curl -s -X POST http://{HTTP_PROVIDER_SERVICENAME}:6116/api/config/{driver.stack_id} -H "Content-Type: application/json" -d @{traefik_http_provider_target} > /dev/nul',
  ])

  entrypoint = ' && '.join([
    # Log the start of the initial endpoint call
    'echo "Starting initial call to the provider endpoint..."',

    # Call the POST endpoint at startup
    register_command,

    # Log the end of the initial endpoint call
    'echo "Initial call to the provider endpoint completed. Running cron job..."',

    # Set up the cron job
    f'echo "*/1 * * * * {register_command}" | crontab -',

    # Start the cron service
    'crond -f',
  ])

  seto_agent_compose_data, _ = resolve_compose_file(
    driver=driver,
    compose_data={
      'services': {
        'seto_agent': {
          'image': 'curlimages/curl:latest',
          'user': 'root',  # Ensure it runs as root to avoid issue `crontab: must be suid to work properly`
          'networks': networks_list,
          'entrypoint': f"sh -c '{entrypoint}'",
          'volumes-image': [
            f'{traefik_http_provider_filename}:{traefik_http_provider_target}',
          ],
        },
      },
    },
  )

  swarm_config['services'].update(seto_agent_compose_data['services'])

  resolve(
    args,
    driver,
    inject=True,
    execute=lambda config, placement: parse_compose_config(
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
      traefik_http_provider_middlewares=traefik_http_provider_middlewares,
    ),
  )

  if not os.path.exists(bridges_path):
    os.mkdir(bridges_path)

  with open(traefik_http_provider_filename, 'w', encoding='utf-8') as file:
    file.write(json.dumps(traefik_http_provider, indent='  '))

  print(f'Building {driver.stack_id} swarm images...')
  # swarm.info()
  swarm.build()

  print(f'Pushing {driver.stack_id} images...')
  swarm.push()

  # print(f'Creating {driver.stack_id} volumes...')
  # driver.create_volumes(replica=replica, volumes=volumes, force=args.force)

  print(f'Deploying {driver.stack_id} swarm environment...')
  swarm.deploy()
  swarm.ps()

  if composes_items:
    print(f'Building {driver.stack_id} compose images...')
    for compose in composes_items:
      # compose.info()
      compose.build()

    print(f'Pulling {driver.stack_id} compose images...')
    for compose in composes_items:
      compose.pull()

    print(f'Deploying {driver.stack_id} compose environment...')
    for compose in composes_items:
      compose.deploy()
      compose.ps()
      compose.logs()
