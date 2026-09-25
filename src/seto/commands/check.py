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

from ..core.docker import DockerCompose
from ..core.driver import Driver
from .resolve import resolve

logger = logging.getLogger(__name__)


def report_nodes(compose: DockerCompose) -> None:
  """Report matched nodes along with their Docker context availability.

  Host capability matching and Docker daemon accessibility are distinct: a node
  may have the requested device and still be unusable as a Docker deployment
  target because it has no Docker context registered by ``seto setup``.
  """
  matched_nodes = compose.matched_nodenames
  deployable_nodes = set(compose.available_nodenames)

  logger.info('Matched and verified nodes:')

  for node in matched_nodes:
    context = compose.contexts.context_for(node)

    if node in deployable_nodes:
      logger.info('  - %s (docker context: %s)', node, context)
    elif context:
      logger.info(
        '  - %s (docker context: %s, excluded: physical device verification failed)', node, context
      )
    else:
      logger.info('  - %s (no Docker context registered - run "seto setup")', node)


def execute_check_command(args, driver: Driver) -> None:
  if args.constraint:
    # Use dynamic constraint from CLI
    compose_config = {
      'x-placement': args.constraint,
    }
    compose = DockerCompose(
      driver=driver,
      config=compose_config,
    )
    logger.info('Nodes matching constraint %s:', args.constraint)
    report_nodes(compose)
  else:
    # Resolve placement from compose file
    setattr(args, 'compose', True)
    composes_items: list[DockerCompose] = []

    def parse_config(config, placement):
      if config['services']:
        compose = DockerCompose(
          driver=driver,
          config=config,
        )
        composes_items.append(compose)

    resolve(
      args,
      driver,
      inject=True,
      execute=parse_config,
      skip_volumes=True,
    )

    if not composes_items:
      logger.info('No compose services or placement constraints found in stack.')
      return

    for compose in composes_items:
      logger.info('Target placement constraints: %s', compose.placement)

      try:
        report_nodes(compose)
      except Exception as exception:
        logger.error('Error matching nodes: %s', exception)
