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
from ..core.command import parse_volumes_args
from ..core.driver import Driver
from ..core.shell import Setting


def execute_create_volumes_command(args, driver: Driver) -> None:
  replica: list[Setting] = [items[0] for items in args.replica]
  all_volumes = parse_volumes_args(args, driver)

  driver.connect()
  driver.create_volumes(replica=replica, volumes=all_volumes, force=args.force)
  driver.apply_manager_changes()
  driver.terminate()
