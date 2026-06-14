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
import logging
import re
from typing import Any

from docker import DockerClient

from ..core.docker import DockerCompose
from ..core.docker import DockerSwarm
from ..core.driver import Driver
from ..core.network import GLOBAL_NETWORKS
from ..core.network import resolve_networks
from ..core.parser import resolve_compose_file
from ..core.shell import Setting
from .config import resolve

logger = logging.getLogger(__name__)


# Define the regular expression pattern to match {{ .Node.Hostname }} with optional spaces
NODE_HOSTNAME_RE = r'\{\{\s*\.Node\.Hostname\s*\}\}'


def parse_service_vars(entries: dict[str, Any], hostname: str) -> None:
  for key, value in entries.items():
    if isinstance(value, str):
      entries[key] = re.sub(NODE_HOSTNAME_RE, hostname, value)


def get_label_value(labels: dict[str, Any], name: str) -> Any | str:
  for label, value in labels.items():
    if label.endswith(name):
      return value
  return ''


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
  placement: str | list[str],
  composes: list[DockerCompose],
) -> None:
  if compose_config['services']:
    compose = DockerCompose(
      client=client,
      driver=driver,
      config=compose_config,
    )

    composes.append(compose)


def deploy_seto_stack(args, driver: Driver, replica: list[Setting]) -> None:
  # Temporary rewrite driver config
  driver.project = 'seto'
  driver.stack = None

  # Building seto config
  client = DockerClient.from_env()

  logger.info('Configuring seto agent...')
  internal_stack = {
    'networks': GLOBAL_NETWORKS,
    'services': {
      'agent': {
        'image': 'busybox',
        'command': ['sleep', 'infinity'],
        'networks': list(GLOBAL_NETWORKS.keys()),
        'deploy': {
          'mode': 'global',
        },
      },
    },
  }

  # Resolving compose local volumes
  resolved_compose_data, _ = resolve_compose_file(
    driver=driver,
    image_prefix=args.image_prefix,
    compose_data=internal_stack,
    default_volume_mount_mode=args.default_volume_mount_mode,
    default_nfs_options=args.default_nfs_options,
    inject=True,
  )

  logger.info('Deploying seto services...')
  swarm = DockerSwarm(
    client=client,
    driver=driver,
    config=resolved_compose_data,
  )

  swarm.info()
  swarm.deploy()
  swarm.ps()
  swarm.logs()

  # Restore initial driver config
  driver.project = args.project
  driver.stack = args.stack


def deploy_project_stack(args, driver: Driver, replica: list[Setting]) -> None:
  # Temporary rewrite driver config
  driver.project = f'seto-{args.project}'
  driver.stack = None

  # Building seto config
  client = DockerClient.from_env()
  project_networks = resolve_networks(args.project)

  logger.info('Configuring seto project agent...')
  internal_stack = {
    'networks': project_networks,
    'services': {
      'agent': {
        'image': 'busybox',
        'command': ['sleep', 'infinity'],
        'networks': list(project_networks.keys()),
        'deploy': {
          'mode': 'global',
        },
      },
    },
  }

  # Resolving compose local volumes
  resolved_compose_data, volumes = resolve_compose_file(
    driver=driver,
    image_prefix=args.image_prefix,
    compose_data=internal_stack,
    default_volume_mount_mode=args.default_volume_mount_mode,
    default_nfs_options=args.default_nfs_options,
    inject=True,
  )

  logger.info('Creating seto project volumes...')
  driver.create_volumes(replica=replica, volumes=volumes, force=args.force)

  logger.info('Deploying seto project services...')
  swarm = DockerSwarm(
    client=client,
    driver=driver,
    config=resolved_compose_data,
  )

  swarm.info()
  swarm.deploy()
  swarm.ps()
  swarm.logs()

  # Restore initial driver config
  driver.project = args.project
  driver.stack = args.stack


def execute_deploy_command(args, driver: Driver) -> None:
  client = DockerClient.from_env()

  logger.info('Resolving %s services...', driver.stack_id)
  setattr(args, 'compose', False)
  swarm_config = resolve(args, driver)

  swarm = DockerSwarm(
    client=client,
    driver=driver,
    config=swarm_config,
  )

  setattr(args, 'compose', True)
  networks_list = list(swarm_config['networks'].keys())
  composes_items: list[DockerCompose] = []

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
    ),
  )

  logger.info('Building %s swarm images...', driver.stack_id)
  swarm.build()

  logger.info('Pushing %s images...', driver.stack_id)
  swarm.push()

  logger.info('Deploying %s swarm environment...', driver.stack_id)

  if logger.level == logging.DEBUG:
    swarm.info()

  swarm.deploy()

  if logger.level == logging.DEBUG:
    swarm.ps()
    swarm.logs()

  if composes_items:
    logger.info('Building %s compose images...', driver.stack_id)

    for compose in composes_items:
      compose.build()
      compose.push()

    logger.info('Pulling %s compose images...', driver.stack_id)

    for compose in composes_items:
      if logger.level == logging.DEBUG:
        compose.info()

      compose.pull()

    logger.info('Deploying %s compose environment...', driver.stack_id)

    for compose in composes_items:
      compose.deploy()

      if logger.level == logging.DEBUG:
        compose.ps()
        compose.logs()
