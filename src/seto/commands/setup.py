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
import os

from ..core.context import DockerContextError
from ..core.context import NodeContext
from ..core.context import SetoConfig
from ..core.driver import Driver
from ..core.network import global_networks_fingerprint
from ..core.shell import Setting
from .deploy import deploy_namespace_stack
from .deploy import deploy_seto_stack

logger = logging.getLogger(__name__)

CONFIG_DIR = '.seto'
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')


def execute_setup_command(args, driver: Driver) -> None:
  storage_replicas: list[Setting] = [items[0] for items in args.storage_replicas]
  clients: list[Setting] = [items[0] for items in args.clients]
  fresh_setup = getattr(args, 'setup_fresh', True)

  config = driver.contexts.config
  fingerprint = global_networks_fingerprint(args.namespace)
  # The namespace agent is the only seto-owned stack that depends on the global
  # networks, so it is the only one a re-run may have to refresh.
  refresh_namespace = fresh_setup or config.global_networks_fingerprint != fingerprint

  # The storage-node SSH connection is only opened when the NFS hosts or the
  # namespace stack actually need work; a re-run that only repairs Docker
  # contexts stays local.
  if refresh_namespace:
    driver.connect()

  try:
    if fresh_setup:
      # Host-level provisioning over the administrative SSH connection (NFS
      # server/client packages, SSH key authorization, Docker daemon access).
      # It only runs while .seto/config.json is missing: a re-run never touches
      # the NFS hosts again.
      generate_ssh_keys(args, driver)
      driver.setup_storage_node(storage_replicas, args.force)
      driver.setup_nodes(clients, args.force)

    # Docker contexts are the canonical way Ṣeto reaches the Docker daemon of
    # each configured node. They are always provisioned so a re-run can repair
    # a context that was removed or went stale.
    failed_nodes = provision_docker_contexts(driver, clients)

    config.driver = driver.slug
    config.storage_base_path = driver.storage_base_path
    write_seto_config(config)

    if failed_nodes:
      nodes = ', '.join(failed_nodes)
      raise DockerContextError(
        f'No usable Docker context could be established for nodes: {nodes}. The Docker contexts '
        f'that were established have been saved in {CONFIG_PATH}; fix the reported errors and '
        f"re-run 'seto setup' to repair the remaining nodes."
      )

    # The seto provider stack is only deployed on a first setup; the namespace
    # stack is redeployed only when the ``networks/`` definitions changed since
    # the fingerprinted deployment.
    if fresh_setup:
      deploy_seto_stack(args, driver)

    if refresh_namespace:
      deploy_namespace_stack(args, driver)
      config.global_networks_fingerprint = fingerprint
      write_seto_config(config)
      driver.apply_storage_node_changes()
    else:
      logger.info('Global networks are unchanged; keeping the namespace deployment as is.')
  finally:
    driver.terminate()


def provision_docker_contexts(driver: Driver, clients: list[Setting]) -> list[str]:
  """Create and verify the Docker context of every configured node.

  Returns the hostnames of the nodes whose context could not be established.
  Successfully verified contexts are recorded in the configuration; failing
  nodes are recorded as incomplete so they are never targeted by a Compose
  deployment until a later ``seto setup`` repairs them. The machine running
  Ṣeto is always mapped to Docker's built-in ``default`` context.
  """
  config = driver.contexts.config
  manager = driver.contexts.manager
  nodes = [
    (NodeContext.from_setting(driver.storage_node.setting), True),
    *[(NodeContext.from_setting(setting), False) for setting in clients],
  ]

  failed_nodes: list[str] = []

  for node, is_storage_node in nodes:
    # Reuse the context name persisted by a previous setup run.
    configured = config.node_for(node.hostname)

    if configured is not None:
      node.docker_context = configured.docker_context

    try:
      context = manager.provision(node)
    except DockerContextError as error:
      logger.error("Error: unable to use node '%s' with Docker: %s", node.hostname, error)
      node.docker_context = None
      failed_nodes.append(node.hostname)
    else:
      node.docker_context = context
      logger.info("Docker context '%s' is ready for node %s.", context, node.hostname)

    config.upsert_node(node, storage_node=is_storage_node)

  return failed_nodes


def write_seto_config(config: SetoConfig) -> None:
  os.makedirs(CONFIG_DIR, exist_ok=True)

  # Automatically add images/ to .seto/.gitignore
  gitignore_path = os.path.join(CONFIG_DIR, '.gitignore')

  if os.path.exists(gitignore_path):
    with open(gitignore_path, encoding='utf-8') as file:
      content = file.read()

    if 'images/' not in content.splitlines() and 'images' not in content.splitlines():
      with open(gitignore_path, 'a', encoding='utf-8') as file:
        file.write('images/\n')
  else:
    with open(gitignore_path, 'w', encoding='utf-8') as file:
      file.write('images/\n')

  config.save(CONFIG_PATH)

  logger.info('Configuration file generated at %s\n', CONFIG_PATH)


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
  storage_replicas: list[Setting] = [items[0] for items in getattr(args, 'storage_replicas', [])]
  clients: list[Setting] = [items[0] for items in getattr(args, 'clients', [])]
  remotes = storage_replicas + [
    item for item in clients if not any(item == setting for setting in storage_replicas)
  ]

  logger.info('Please run the following command for each server to copy your public key:\n')

  if remotes:
    for setting in remotes:
      logger.info(
        '\tssh-copy-id -i %s %s@%s',
        pub_key_file_path,
        setting.username or '<user>',
        setting.hostname,
      )
  else:
    logger.info('\tssh-copy-id -i %s <user>@server1', pub_key_file_path)
    logger.info('\tssh-copy-id -i %s <user>@server2', pub_key_file_path)
    logger.info('\tssh-copy-id -i %s <user>@server3', pub_key_file_path)

  logger.info('\nAfter copying the SSH keys, re-run the command to try again.')
