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
import argparse
import glob
import json
import logging
import os
import socket
import sys

from paramiko.ssh_exception import AuthenticationException

from .commands.check import execute_check_command
from .commands.config import execute_config_command
from .commands.deploy import execute_deploy_command
from .commands.down import execute_down_command
from .commands.mount import execute_mount_volumes_command
from .commands.setup import execute_setup_command
from .commands.setup import print_ssh_copy_id_commands
from .commands.umount import execute_umount_volumes_command
from .commands.volumes import execute_create_volumes_command
from .core.package import APP_DESCRIPTION
from .core.package import APP_NAME
from .core.package import APP_VERSION
from .core.shell import Setting
from .drivers.nfs import NFSDriver
from .shells.local import LocalShell
from .shells.remote import RemoteShell

DriverType = str
ShellConnectionString = str


driver_shemes = ['nfs://']
driver_examples = [f'{sheme}username:password@hostname' for sheme in driver_shemes]
DRIVER_EXAMPLES_STR = ' or '.join(driver_examples)


def storage_node_type(value: str) -> tuple[DriverType, ShellConnectionString]:
  if not any(value.startswith(scheme) for scheme in driver_shemes):
    raise argparse.ArgumentTypeError(
      f'{value} is not a valid storage node ({DRIVER_EXAMPLES_STR})',
    )

  driver_type, connection_string = value.split('://', 1)

  return driver_type, connection_string


def storage_replica_type(value: str) -> list[Setting]:
  connection_strings = value.split(' ')

  try:
    return [Setting.from_connection_string(item.strip()) for item in connection_strings]
  except socket.gaierror as error:
    raise argparse.ArgumentTypeError(f'{value} - {error}') from error
  except Exception as exception:
    raise argparse.ArgumentTypeError(
      f'{value} is not a storage replica value (username:password@hostname). {exception}'
    ) from exception


def nodes_type(value: str) -> list[Setting]:
  connection_strings = value.split(' ')

  try:
    return [Setting.from_connection_string(item.strip()) for item in connection_strings]
  except socket.gaierror as error:
    raise argparse.ArgumentTypeError(f'{value} - {error}') from error
  except Exception as exception:
    raise argparse.ArgumentTypeError(
      f'{value} is not a nodes value (username:password@hostname)'
    ) from exception


logger = logging.getLogger(__name__)


def setup_logging(debug: bool = False) -> None:
  log_level = logging.DEBUG if debug else logging.INFO
  formatter = logging.Formatter('%(levelname)s: %(message)s' if debug else '%(message)s')

  # Stdout handler for INFO and DEBUG
  stdout_handler = logging.StreamHandler(sys.stdout)
  stdout_handler.setLevel(log_level)
  stdout_handler.setFormatter(formatter)

  # Limit stdout to INFO level and below
  class MaxLevelFilter(logging.Filter):
    def filter(self, record):
      return record.levelno <= logging.INFO

  stdout_handler.addFilter(MaxLevelFilter())

  # Stderr handler for WARNING, ERROR, and CRITICAL
  stderr_handler = logging.StreamHandler(sys.stderr)
  stderr_handler.setLevel(logging.WARNING)
  stderr_handler.setFormatter(formatter)

  root_logger = logging.getLogger()
  root_logger.setLevel(log_level)
  root_logger.handlers = []
  root_logger.addHandler(stdout_handler)
  root_logger.addHandler(stderr_handler)


def run() -> None:
  parser = argparse.ArgumentParser(APP_NAME, description=APP_DESCRIPTION)
  subparsers = parser.add_subparsers()

  parser.add_argument('--project', help='Project name')
  parser.add_argument('--stack', help='Stack name')

  # add version argument
  parser.add_argument(
    '-v',
    '--version',
    action='version',
    version=f'{APP_NAME} {APP_VERSION}',
    help='Show the version number and exit',
  )

  parser.add_argument(
    '--key',
    default=os.path.expanduser('~/.ssh/id_rsa'),
    help='Path to the SSH private key file',
  )

  parser.add_argument(
    '--debug',
    action='store_true',
    help='Enable debug mode',
  )

  #
  # Setup command
  setup_parser = subparsers.add_parser(
    'setup', description='Setup storage node and storage replica nodes'
  )
  setup_parser.set_defaults(func=execute_setup_command)

  setup_parser.add_argument(
    '--storage-node',
    dest='storage_node',
    type=storage_node_type,
    required=True,
    help=f'Storage node URI to use. Can be {DRIVER_EXAMPLES_STR}',
  )

  setup_parser.add_argument(
    '--storage-replicas',
    dest='storage_replicas',
    type=storage_replica_type,
    nargs='+',
    required=True,
    help='Storage replica nodes to setup',
  )

  setup_parser.add_argument(
    '--clients',
    type=nodes_type,
    nargs='+',
    required=True,
    help='Client nodes to setup',
  )

  setup_parser.add_argument(
    '--force',
    action='store_true',
    help='Force setup tasks',
  )

  #
  # Create Volumes command
  create_volumes_parser = subparsers.add_parser(
    'create-volumes', description='Create and sync shared volumes'
  )
  create_volumes_parser.set_defaults(func=execute_create_volumes_command)

  create_volumes_parser.add_argument(
    '--sync',
    action='store_true',
    help='Sync volumes data',
  )

  #
  # Mount Volumes command
  mount_volumes_parser = subparsers.add_parser('mount-volumes', description='Mount shared volumes')
  mount_volumes_parser.set_defaults(func=execute_mount_volumes_command)
  mount_volumes_parser.add_argument(
    '--clients',
    type=nodes_type,
    nargs='+',
    required=True,
    help='Client nodes on which volumes will be mounted',
  )

  #
  # Unmount Volumes command
  umount_volumes_parser = subparsers.add_parser(
    'unmount-volumes', description='Unmount shared volumes'
  )
  umount_volumes_parser.set_defaults(func=execute_umount_volumes_command)
  umount_volumes_parser.add_argument(
    '--clients',
    type=nodes_type,
    nargs='+',
    required=True,
    help='Client nodes on which volumes will be unmounted',
  )

  #
  # Config command
  config_parser = subparsers.add_parser(
    'config', description='Parse, resolve and render compose file in canonical format'
  )
  config_parser.set_defaults(func=execute_config_command)

  config_parser.add_argument(
    '--compose',
    action='store_true',
    help='Resolve for Docker Compose',
  )

  #
  # Deploy command
  deploy_parser = subparsers.add_parser(
    'deploy', description='Deploy a new stack or update an existing stack'
  )
  deploy_parser.set_defaults(func=execute_deploy_command)

  deploy_parser.add_argument(
    '--image-prefix', default='', help='Image namespace prefix to added to internal images'
  )

  deploy_parser.add_argument(
    '--sync',
    action='store_true',
    help='Sync volumes data before deploying',
  )

  #
  # Down command
  down_parser = subparsers.add_parser('down', description='Stop and remove containers, networks')
  down_parser.set_defaults(func=execute_down_command)

  #
  # Check command
  check_parser = subparsers.add_parser(
    'check', description='Verify and list nodes matching placement constraints'
  )
  check_parser.set_defaults(func=execute_check_command)
  check_parser.add_argument(
    '--constraint',
    action='append',
    help='Dynamic constraint to match against (e.g., host.device.gpu == true). Can be specified multiple times.',
  )

  #
  # Parsing
  try:
    args = parser.parse_args()
  except (Exception, argparse.ArgumentError) as exception:
    setup_logging(False)
    logger.error(exception)
    sys.exit(40)

  setup_logging(args.debug)

  # Configure default volume mount values from environment for volumes-nfs entries
  setattr(
    args,
    'default_volume_mount_mode',
    os.getenv('SETO_DEFAULT_VOLUME_MOUNT_MODE', 'rw'),
  )
  setattr(
    args,
    'default_nfs_options',
    os.getenv('SETO_DEFAULT_NFS_OPTIONS', 'rw,hard,noatime,nodiratime'),
  )

  # Call the function associated with the selected subcommand
  if hasattr(args, 'func'):
    if args.func != execute_check_command:
      if not args.project:
        parser.error('the following arguments are required: --project')
    else:
      if not getattr(args, 'constraint', None):
        if not args.project:
          parser.error('the following arguments are required: --project')
      else:
        if not args.project:
          args.project = os.path.basename(os.getcwd())

    if not hasattr(args, 'image_prefix'):
      setattr(args, 'image_prefix', '')

    if args.func == execute_setup_command:
      driver_type, connection_string = args.storage_node
      setting = Setting.from_connection_string(connection_string)
    else:
      # Determine if the config file is needed (i.e. if volumes-nfs needs parsing)
      needs_nfs = False

      if args.func in (
        execute_create_volumes_command,
        execute_mount_volumes_command,
        execute_umount_volumes_command,
      ):
        needs_nfs = True
      elif args.func != execute_check_command:
        stack_dir = args.stack or args.project

        if stack_dir and os.path.isdir(stack_dir):
          for filepath in glob.glob(os.path.join(stack_dir, '*.yaml')):
            try:
              with open(filepath, encoding='utf-8') as file:
                if 'volumes-nfs' in file.read():
                  needs_nfs = True
                  break
            except Exception:
              pass

      config_path = os.path.join('.seto', 'config.json')

      if needs_nfs:
        if not os.path.exists(config_path):
          logger.error(
            "Error: Configuration file '%s' not found. The setup command has not yet been performed.",
            config_path,
          )
          sys.exit(40)

        try:
          with open(config_path, encoding='utf-8') as file:
            config_data = json.load(file)

          storage_node_cfg = config_data.get('storage_node') or config_data.get('manager')
          driver_type = config_data.get('driver', 'nfs')
          setting = Setting(
            hostname=storage_node_cfg['hostname'],
            ip=storage_node_cfg['ip'],
            local=storage_node_cfg['local'],
            username=None if storage_node_cfg['local'] else NFSDriver.setouser,
            password=None,
          )
        except Exception as exception:
          logger.error('Error: Failed to load configuration file. %s', exception)
          sys.exit(40)
      else:
        # Fallback setting when NFS is not needed/used, so config file is not required.
        driver_type = 'nfs'
        setting = Setting(
          hostname='localhost',
          ip='127.0.0.1',
          local=True,
          username=None,
          password=None,
        )

    locale_setting = (
      setting
      if setting.local
      else Setting(hostname='localhost', local=True, ip='127.0.0.1', username=None, password=None)
    )

    storage_node_shell = (
      LocalShell(setting, args.key) if setting.local else RemoteShell(setting, args.key)
    )
    local_shell = LocalShell(locale_setting, args.key)

    if driver_type == 'nfs':
      driver = NFSDriver(
        stack=args.stack,
        project=args.project,
        host=local_shell,
        storage_node=storage_node_shell,
        debug=args.debug,
      )
    else:
      logger.error("Error: Unknown driver type '%s'", driver_type)
      sys.exit(40)

    if args.debug:
      args.func(args, driver)
    else:
      try:
        args.func(args, driver)
      except AuthenticationException as exception:
        logger.error('\nError: %s', exception)
        print_ssh_copy_id_commands(args)
        sys.exit(41)
      except Exception as exception:
        logger.error(exception)
        sys.exit(50)
  else:
    parser.print_help()

  sys.exit(0)
