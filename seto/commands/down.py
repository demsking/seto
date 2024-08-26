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
from docker import DockerClient

from ..core.docker import DockerCompose
from ..core.docker import DockerSwarm
from ..core.driver import Driver
from .config import resolve


def execute_down_command(args, driver: Driver) -> None:
  client = DockerClient.from_env()

  # Docker Swarm
  print(f'Stoping {driver.stack} environment...')
  setattr(args, 'compose', False)

  swarm = DockerSwarm(
    stack=driver.stack,
    client=client,
    driver=driver,
    config=resolve(args, driver),
  )

  swarm.down()

  # Docker Compose
  setattr(args, 'compose', True)
  config = resolve(args, driver, inject=True)

  if config['services']:
    compose = DockerCompose(
      stack=driver.stack,
      client=client,
      driver=driver,
      config=config,
    )

    compose.down()
