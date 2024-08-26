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
import os

from ..core.driver import Driver
from ..core.shell import Setting
from .deploy import deploy_seto_stack


def execute_setup_command(args, driver: Driver) -> None:
  replica: list[Setting] = [items[0] for items in args.replica]
  clients: list[Setting] = [items[0] for items in args.clients]

  driver.connect()
  generate_ssh_keys(args, driver)
  driver.setup_manager(replica, args.force)
  driver.setup_nodes(clients, args.force)
  deploy_seto_stack(args, driver)
  driver.terminate()


def generate_ssh_keys(args, driver: Driver) -> None:
  key_file_path = args.key

  if not os.path.exists(key_file_path):
    key_file_dir = os.path.dirname(key_file_path)

    if not os.path.exists(key_file_dir):
      driver.shell.mkdir(key_file_dir)

    print(f'Generating SSH key pair at {key_file_path}')
    driver.shell.run(f'ssh-keygen -t rsa -b 4096 -f {key_file_path}')
    print('SSH key pair generated.\n')
    print_ssh_copy_id_commands(args)


def print_ssh_copy_id_commands(args) -> None:
  pub_key_file_path = args.key + '.pub'
  replica: list[Setting] = [items[0] for items in args.replica]
  clients: list[Setting] = [items[0] for items in args.clients]
  remotes = replica + [
    item for item in clients if not any(item == setting for setting in replica)
  ]

  print('Please run the following command for each server to copy your public key:\n')

  if remotes:
    for setting in remotes:
      print(f'\tssh-copy-id -i {pub_key_file_path} {Driver.setouser}@{setting.hostname}')
  else:
    print(f'\tssh-copy-id -i {pub_key_file_path} {Driver.setouser}@server1')
    print(f'\tssh-copy-id -i {pub_key_file_path} {Driver.setouser}@server2')
    print(f'\tssh-copy-id -i {pub_key_file_path} {Driver.setouser}@server3')

  print('\nAfter copying the SSH keys, re-run the command to try again.')
