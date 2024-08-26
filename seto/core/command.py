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
from .driver import Driver
from .parser import parse_services
from .volume import Volume


def parse_volumes_args(args, driver: Driver) -> list[Volume]:
  all_volumes: list[Volume] = []

  parse_services(
    driver=driver,
    stack=args.stack or args.project,
    execute=lambda resolved_compose_data, volumes: all_volumes.extend(volumes),
  )

  return all_volumes
