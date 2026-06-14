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
from docker import DockerClient

from ..core.docker import DockerCompose
from ..core.driver import Driver
from .config import resolve


def execute_check_command(args, driver: Driver) -> None:
  client = DockerClient.from_env()

  if args.constraint:
    # Use dynamic constraint from CLI
    compose_config = {
      'x-placement': args.constraint,
    }
    compose = DockerCompose(
      client=client,
      driver=driver,
      config=compose_config,
    )
    nodes = compose.nodenames
    print(f'Nodes matching constraint {args.constraint}:')
    for node in nodes:
      print(f'  - {node}')
  else:
    # Resolve placement from compose file
    setattr(args, 'compose', True)
    composes_items: list[DockerCompose] = []

    def parse_config(config, placement):
      if config['services']:
        compose = DockerCompose(
          client=client,
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
      print('No compose services or placement constraints found in stack.')
      return

    for compose in composes_items:
      print(f'Target placement constraints: {compose.placement}')

      try:
        nodes = compose.nodenames
        print('Matched and verified nodes:')

        for node in nodes:
          print(f'  - {node}')
      except Exception as exception:
        print(f'Error matching nodes: {exception}')
