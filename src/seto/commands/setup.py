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
import json
import logging
import os

from ..core.driver import Driver
from ..core.shell import Setting
from .deploy import deploy_seto_stack

logger = logging.getLogger(__name__)


def execute_setup_command(args, driver: Driver) -> None:
  storage_replicas: list[Setting] = [items[0] for items in args.storage_replicas]
  clients: list[Setting] = [items[0] for items in args.clients]

  driver.connect()
  deploy_seto_stack(args, driver)
  generate_ssh_keys(args, driver)
  driver.setup_storage_node(storage_replicas, args.force)
  driver.setup_nodes(clients, args.force)
  driver.apply_storage_node_changes()

  config_dir = '.seto'
  os.makedirs(config_dir, exist_ok=True)

  # Automatically add images/ to .seto/.gitignore
  gitignore_path = os.path.join(config_dir, '.gitignore')

  if os.path.exists(gitignore_path):
    with open(gitignore_path, encoding='utf-8') as file:
      content = file.read()

    if 'images/' not in content.splitlines() and 'images' not in content.splitlines():
      with open(gitignore_path, 'a', encoding='utf-8') as file:
        file.write('images/\n')
  else:
    with open(gitignore_path, 'w', encoding='utf-8') as file:
      file.write('images/\n')

  config_path = os.path.join(config_dir, 'config.json')
  config_data = {
    'driver': driver.slug,
    'storage_base_path': driver.storage_base_path,
    'storage_node': {
      'hostname': driver.storage_node.setting.hostname,
      'username': driver.storage_node.setting.username,
      'ip': driver.storage_node.setting.ip,
      'local': driver.storage_node.setting.local,
    },
  }

  with open(config_path, 'w', encoding='utf-8') as file:
    json.dump(config_data, file, indent=2)

  logger.info('Configuration file generated at %s\n', config_path)

  driver.terminate()


def generate_ssh_keys(args, driver: Driver) -> None:
  key_file_path = args.ssh_key

  if not os.path.exists(key_file_path):
    key_file_dir = os.path.dirname(key_file_path)

    if not os.path.exists(key_file_dir):
      driver.shell.mkdir(key_file_dir)

    logger.info('Generating SSH key pair at %s', key_file_path)
    driver.shell.run(f'ssh-keygen -t rsa -b 4096 -f {key_file_path}')
    logger.info('SSH key pair generated.\n')
    print_ssh_copy_id_commands(args)


def print_ssh_copy_id_commands(args) -> None:
  pub_key_file_path = args.ssh_key + '.pub'
  storage_replicas: list[Setting] = [items[0] for items in args.storage_replicas]
  clients: list[Setting] = [items[0] for items in args.clients]
  remotes = storage_replicas + [
    item for item in clients if not any(item == setting for setting in storage_replicas)
  ]

  logger.info('Please run the following command for each server to copy your public key:\n')

  if remotes:
    for setting in remotes:
      logger.info('\tssh-copy-id -i %s %s@%s', pub_key_file_path, Driver.setouser, setting.hostname)
  else:
    logger.info('\tssh-copy-id -i %s %s@server1', pub_key_file_path, Driver.setouser)
    logger.info('\tssh-copy-id -i %s %s@server2', pub_key_file_path, Driver.setouser)
    logger.info('\tssh-copy-id -i %s %s@server3', pub_key_file_path, Driver.setouser)

  logger.info('\nAfter copying the SSH keys, re-run the command to try again.')
