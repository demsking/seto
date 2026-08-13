# Copyright 2026 Sébastien Demanou. All Rights Reserved.
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
# pylint: disable=import-error,invalid-name,redefined-outer-name,no-member,use-implicit-booleaness-not-comparison
import argparse
import socket
from argparse import Namespace
from unittest.mock import MagicMock

import pytest
import seto
from seto import nodes_type
from seto import storage_node_type
from seto import storage_replica_type
from seto.commands.deploy import execute_deploy_command
from seto.commands.deploy import parse_compose_config


def test_storage_node_type(mocker):
  # Valid
  driver, conn = storage_node_type('nfs://admin:secret@host')
  assert driver == 'nfs'
  assert conn == 'admin:secret@host'

  # Invalid scheme
  with pytest.raises(argparse.ArgumentTypeError, match='not a valid storage node'):
    storage_node_type('invalid://admin:secret@host')


def test_storage_replica_type(mocker):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.10')

  # Valid single
  settings = storage_replica_type('admin@host1')
  assert len(settings) == 1
  assert settings[0].hostname == 'host1'

  # Valid multiple (space-separated)
  settings = storage_replica_type('admin@host1 user@host2')
  assert len(settings) == 2
  assert settings[0].hostname == 'host1'
  assert settings[1].hostname == 'host2'

  # Socket error
  mocker.patch(
    'seto.core.shell.resolve_hostname', side_effect=socket.gaierror('mock resolution error')
  )
  with pytest.raises(argparse.ArgumentTypeError, match='mock resolution error'):
    storage_replica_type('admin@host1')

  # Other exceptions
  with pytest.raises(argparse.ArgumentTypeError, match='is not a storage replica value'):
    storage_replica_type('invalid_connection_string_without_at')


def test_nodes_type(mocker):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.11')

  # Valid single
  settings = nodes_type('admin@client1')
  assert len(settings) == 1
  assert settings[0].hostname == 'client1'

  # Valid multiple
  settings = nodes_type('admin@client1 user@client2')
  assert len(settings) == 2
  assert settings[0].hostname == 'client1'

  # Socket error
  mocker.patch(
    'seto.core.shell.resolve_hostname', side_effect=socket.gaierror('mock resolution error')
  )
  with pytest.raises(argparse.ArgumentTypeError, match='mock resolution error'):
    nodes_type('admin@client1')

  # Other exceptions
  with pytest.raises(argparse.ArgumentTypeError, match='is not a nodes value'):
    nodes_type('invalid_connection_string')


def test_deploy_syncs_volumes_by_default(mocker):
  execute_deploy_command = mocker.patch('seto.execute_deploy_command')
  mocker.patch('sys.argv', ['seto', '--namespace', 'test', '--stack', 'app', 'deploy'])

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  args, _driver = execute_deploy_command.call_args.args
  assert args.sync is True


def test_deploy_no_sync_disables_volume_sync(mocker):
  execute_deploy_command = mocker.patch('seto.execute_deploy_command')
  mocker.patch('sys.argv', ['seto', '--namespace', 'test', '--stack', 'app', 'deploy', '--no-sync'])

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  args, _driver = execute_deploy_command.call_args.args
  assert args.sync is False


def test_deploy_force_overwrites_existing_volume_files(mocker):
  execute_deploy_command = mocker.patch('seto.execute_deploy_command')
  mocker.patch('sys.argv', ['seto', '--namespace', 'test', '--stack', 'app', 'deploy', '--force'])

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  args, _driver = execute_deploy_command.call_args.args
  assert args.force is True


def test_storage_base_path_setup_arg_is_propagated_to_driver(mocker):
  mocker.patch('seto.execute_setup_command')
  nfs_driver = mocker.patch('seto.NFSDriver')
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.20')
  mocker.patch(
    'sys.argv',
    [
      'seto',
      '--namespace',
      'test',
      '--stack',
      'app',
      'setup',
      '--storage-path',
      '/srv/nfs',
      '--storage-node',
      'nfs://admin@host',
      '--storage-replicas',
      'user@replica1',
      '--clients',
      'user@client1',
    ],
  )

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  assert nfs_driver.call_args.kwargs['storage_base_path'] == '/srv/nfs'


def test_deploy_compose_only_stack_does_not_deploy_swarm(mocker):
  args = Namespace(
    namespace='test',
    stack='app',
    image_prefix='',
    default_volume_mount_mode='rw',
    default_nfs_options='rw,hard,noatime,nodiratime,nfsvers=4',
    sync=True,
    force=False,
  )
  driver = MagicMock()
  driver.stack_id = 'test_app'

  mocker.patch('seto.commands.deploy.parse_volumes_args', return_value=[])
  mocker.patch('seto.commands.deploy.DockerClient.from_env')
  refresh_provider = mocker.patch('seto.commands.deploy.deploy_seto_stack')
  resolved_configs = [
    {
      'x-placement': None,
      'configs': {},
      'networks': {},
      'volumes': {},
      'secrets': {},
      'services': {},
    },
    {
      'x-placement': ['host.name == worker'],
      'configs': {},
      'networks': {},
      'volumes': {},
      'secrets': {},
      'services': {'server': {'image': 'nginx'}},
    },
  ]

  def resolve_side_effect(*_args, **kwargs):
    resolved = resolved_configs.pop(0)
    execute = kwargs.get('execute')

    if execute and resolved['services']:
      execute(resolved, resolved['x-placement'] or [])

    return resolved

  resolve = mocker.patch('seto.commands.deploy.resolve', side_effect=resolve_side_effect)
  swarm_cls = mocker.patch('seto.commands.deploy.DockerSwarm')
  compose_cls = mocker.patch('seto.commands.deploy.DockerCompose')

  execute_deploy_command(args, driver)

  refresh_provider.assert_called_once_with(args, driver)

  swarm = swarm_cls.return_value
  swarm.build.assert_not_called()
  swarm.push.assert_not_called()
  swarm.deploy.assert_not_called()
  compose = compose_cls.return_value
  compose.build.assert_called_once()
  compose.push.assert_called_once()
  compose.pull.assert_called_once()
  compose.deploy.assert_called_once()
  assert resolve.call_args_list[0].args[:2] == (args, driver)
  assert args.compose is True


def test_parse_compose_config_injects_seto_agent(mocker):
  args = Namespace()
  driver = MagicMock()
  driver.stack_id = 'demo_stack'
  client = MagicMock()

  compose_config = {
    'services': {
      'app': {
        'image': 'nginx:alpine',
      }
    },
    'networks': {},
  }
  composes: list = []

  compose_cls = mocker.patch('seto.commands.deploy.DockerCompose')

  parse_compose_config(
    args,
    driver,
    client,
    [],
    {},
    compose_config=compose_config,
    placement=[],
    composes=composes,
  )

  assert 'seto-agent' not in compose_config['services']
  assert compose_config['networks'] == {}

  compose_cls.assert_called_once()
  assert composes == [compose_cls.return_value]
