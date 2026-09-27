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
import json
import socket
from argparse import Namespace
from unittest.mock import MagicMock

import pytest
import seto
from seto import nodes_type
from seto import storage_node_type
from seto import storage_replica_type
from seto.commands.deploy import deploy_stack_images
from seto.commands.deploy import execute_deploy_command
from seto.commands.deploy import parse_compose_config
from seto.commands.deploy import pull_and_push_images
from seto.core.docker import DockerCompose


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
  manager_client = MagicMock()
  driver.contexts.manager_client.return_value = manager_client

  mocker.patch('seto.commands.deploy.parse_volumes_args', return_value=[])
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
  compose_cls.return_value.has_builds = True

  execute_deploy_command(args, driver)

  refresh_provider.assert_called_once_with(args, driver)

  # Swarm node discovery and Compose deployments use the manager Docker context
  driver.contexts.manager_client.assert_called_once_with()
  assert swarm_cls.call_args.kwargs['client'] is manager_client
  assert compose_cls.call_args.kwargs['client'] is manager_client

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


def test_deploy_skips_compose_build_without_build_entry(mocker):
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
  driver.contexts.manager_client.return_value = MagicMock()

  mocker.patch('seto.commands.deploy.parse_volumes_args', return_value=[])
  mocker.patch('seto.commands.deploy.deploy_seto_stack')
  swarm_config = {
    'x-placement': None,
    'configs': {},
    'networks': {},
    'volumes': {},
    'secrets': {},
    'services': {'server': {'image': 'nginx'}},
  }
  compose_config = {
    'x-placement': ['host.name == worker'],
    'configs': {},
    'networks': {},
    'volumes': {},
    'secrets': {},
    'services': {'worker': {'image': 'busybox'}},
  }
  resolved_configs = [swarm_config, compose_config]

  def resolve_side_effect(*_args, **kwargs):
    resolved = resolved_configs.pop(0)
    execute = kwargs.get('execute')

    if execute and resolved['services']:
      execute(resolved, resolved['x-placement'] or [])

    return resolved

  mocker.patch('seto.commands.deploy.resolve', side_effect=resolve_side_effect)
  swarm_cls = mocker.patch('seto.commands.deploy.DockerSwarm')
  compose_cls = mocker.patch('seto.commands.deploy.DockerCompose')
  swarm_cls.return_value.has_builds = False
  compose_cls.return_value.has_builds = False

  execute_deploy_command(args, driver)

  # No service declares a `build` entry: nothing to build, so neither `compose
  # build` nor `compose push` may run — the images comes from the registry.
  swarm_cls.return_value.build.assert_not_called()
  swarm_cls.return_value.push.assert_not_called()
  compose_cls.return_value.build.assert_not_called()
  compose_cls.return_value.push.assert_not_called()

  # The deploy itself still happens.
  swarm_cls.return_value.deploy.assert_called_once()
  compose_cls.return_value.deploy.assert_called_once()


def test_deploy_builds_swarm_when_a_service_declares_build(mocker):
  args = Namespace(
    namespace='test',
    stack='app',
    image_prefix='registry.example.com/',
    default_volume_mount_mode='rw',
    default_nfs_options='rw,hard,noatime,nodiratime,nfsvers=4',
    sync=True,
    force=False,
  )
  driver = MagicMock()
  driver.stack_id = 'test_app'
  driver.contexts.manager_client.return_value = MagicMock()

  mocker.patch('seto.commands.deploy.parse_volumes_args', return_value=[])
  mocker.patch('seto.commands.deploy.deploy_seto_stack')
  resolve = mocker.patch(
    'seto.commands.deploy.resolve',
    return_value={
      'x-placement': None,
      'configs': {},
      'networks': {},
      'volumes': {},
      'secrets': {},
      'services': {
        'api': {
          'image': 'registry.example.com/app-api:1.0',
          'build': {'context': '.', 'dockerfile': '.seto/images/app-api.dockerfile'},
        }
      },
    },
  )
  swarm_cls = mocker.patch('seto.commands.deploy.DockerSwarm')
  mocker.patch('seto.commands.deploy.DockerCompose')
  swarm_cls.return_value.has_builds = True

  execute_deploy_command(args, driver)

  assert resolve.call_count == 2
  swarm_cls.return_value.build.assert_called_once()
  swarm_cls.return_value.push.assert_called_once()


def test_deploy_stack_images_builds_only_when_a_build_entry_is_declared(mocker):
  stack = MagicMock(spec=DockerCompose)
  stack.has_builds = False

  deploy_stack_images(stack, 'test_app')

  stack.build.assert_not_called()
  stack.push.assert_not_called()

  stack.has_builds = True
  deploy_stack_images(stack, 'test_app')

  stack.build.assert_called_once()
  stack.push.assert_called_once()


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


def test_pull_and_push_images_deduplicates_image_shared_by_services(mocker):
  run = mocker.patch('seto.commands.deploy.subprocess.run')
  config = {
    'services': {
      'netdata-a': {'image': 'netdata/netdata:stable'},
      'netdata-b': {'image': 'netdata/netdata:stable'},
      'netdata-c': {'image': 'netdata/netdata:stable'},
    }
  }

  pull_and_push_images([config], 'registry.umsizi.net/')

  run.assert_called_once()
  assert run.call_args.args[0] == [
    'docker',
    'buildx',
    'imagetools',
    'create',
    '--tag',
    'registry.umsizi.net/netdata:stable',
    'netdata/netdata:stable',
  ]

  for service in config['services'].values():
    assert service['image'] == 'registry.umsizi.net/netdata:stable'


def test_pull_and_push_images_deduplicates_image_across_configs(mocker):
  run = mocker.patch('seto.commands.deploy.subprocess.run')
  swarm_config = {'services': {'monitor': {'image': 'netdata/netdata:stable'}}}
  compose_config = {'services': {'agent': {'image': 'netdata/netdata:stable'}}}

  pull_and_push_images([swarm_config, compose_config], 'registry.local/')

  run.assert_called_once()


def test_pull_and_push_images_merges_platforms_of_shared_image(mocker):
  run = mocker.patch('seto.commands.deploy.subprocess.run')
  config = {
    'services': {
      'a': {
        'image': 'example/app:1.0',
        'deploy': {'placement': {'constraints': ['node.platform.arch == amd64']}},
      },
      'b': {
        'image': 'example/app:1.0',
        'deploy': {'placement': {'constraints': ['node.platform.arch == arm64']}},
      },
      'c': {
        'image': 'example/app:1.0',
        'deploy': {'placement': {'constraints': ['node.platform.arch == amd64']}},
      },
    }
  }

  pull_and_push_images([config], 'registry.local/')

  run.assert_called_once()
  assert run.call_args.args[0] == [
    'docker',
    'buildx',
    'imagetools',
    'create',
    '--tag',
    'registry.local/app:1.0',
    '--platform',
    'linux/amd64,linux/arm64',
    'example/app:1.0',
  ]


def test_pull_and_push_images_all_platforms_wins_over_constraints(mocker):
  run = mocker.patch('seto.commands.deploy.subprocess.run')
  config = {
    'services': {
      'a': {
        'image': 'example/app:1.0',
        'deploy': {'placement': {'constraints': ['node.platform.arch == amd64']}},
      },
      'b': {'image': 'example/app:1.0'},
    }
  }

  pull_and_push_images([config], 'registry.local/')

  run.assert_called_once()
  assert '--platform' not in run.call_args.args[0]


def test_pull_and_push_images_mirrors_distinct_images_separately(mocker):
  run = mocker.patch('seto.commands.deploy.subprocess.run')
  config = {'services': {'a': {'image': 'nginx:1'}, 'b': {'image': 'redis:7'}}}

  pull_and_push_images([config], 'registry.local/')

  assert run.call_count == 2


def test_pull_and_push_images_skips_plain_images_without_prefix(mocker):
  run = mocker.patch('seto.commands.deploy.subprocess.run')
  config = {'services': {'a': {'image': 'nginx:1'}}}

  pull_and_push_images([config], '')

  run.assert_not_called()
  assert config['services']['a']['image'] == 'nginx:1'


def test_pull_and_push_images_reads_from_line_for_generated_images(mocker, tmp_path):
  run = mocker.patch('seto.commands.deploy.subprocess.run')
  dockerfile = tmp_path / 'app.dockerfile'
  dockerfile.write_text('FROM netdata/netdata:stable\nCOPY ./conf /conf\n', encoding='utf-8')
  config = {
    'services': {
      'a': {
        'image': 'registry.local/a:stable',
        'build': {'context': '.', 'dockerfile': str(dockerfile)},
      },
      'b': {
        'image': 'registry.local/b:stable',
        'build': {'context': '.', 'dockerfile': str(dockerfile)},
      },
    }
  }

  pull_and_push_images([config], 'registry.local/')

  assert run.call_count == 2
  assert all('build' not in service for service in config['services'].values())


def write_config_file(path, data):
  directory = path / '.seto'
  directory.mkdir(parents=True, exist_ok=True)
  (directory / 'config.json').write_text(json.dumps(data), encoding='utf-8')


def test_check_command_reloads_the_persisted_docker_context_mapping(mocker, tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  write_config_file(
    tmp_path,
    {
      'version': 2,
      'driver': 'nfs',
      'storage_node': {
        'hostname': 'manager1',
        'username': 'admin',
        'ip': '10.0.0.1',
        'local': False,
        'docker_context': 'seto-manager1',
      },
      'nodes': [
        {
          'hostname': 'client1',
          'username': 'admin',
          'ip': '10.0.0.2',
          'local': False,
          'docker_context': 'seto-client1',
        }
      ],
    },
  )

  check = mocker.patch('seto.execute_check_command')
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.20')
  mocker.patch('sys.argv', ['seto', 'check', '--constraint', 'host.device.gpu == true'])

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  args, driver = check.call_args.args
  assert args.namespace == tmp_path.name
  assert driver.contexts.context_for('client1') == 'seto-client1'
  assert driver.contexts.context_for('manager1') == 'seto-manager1'
  assert driver.contexts.manager_context() == 'seto-manager1'


def test_check_command_does_not_require_the_configuration_file(mocker, tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)

  check = mocker.patch('seto.execute_check_command')
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.20')
  mocker.patch('sys.argv', ['seto', 'check', '--constraint', 'host.device.gpu == true'])

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  _args, driver = check.call_args.args
  assert driver.contexts.context_for('client1') is None
  assert driver.contexts.manager_context() is None


def test_nfs_stack_requires_the_setup_configuration(mocker, tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  stack_dir = tmp_path / 'app'
  stack_dir.mkdir()
  (stack_dir / 'app.yaml').write_text(
    'services:\n  web:\n    image: nginx\n    volumes-nfs:\n      - ./data:/data\n',
    encoding='utf-8',
  )

  deploy = mocker.patch('seto.execute_deploy_command')
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.20')
  mocker.patch('sys.argv', ['seto', '--namespace', 'app', '--stack', 'app', 'deploy'])

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 40
  deploy.assert_not_called()


def test_setup_command_reuses_the_existing_configuration(mocker, tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  write_config_file(
    tmp_path,
    {
      'version': 2,
      'driver': 'nfs',
      'storage_node': {
        'hostname': 'manager1',
        'username': 'admin',
        'ip': '10.0.0.1',
        'local': False,
        'docker_context': 'seto-manager1',
      },
      'nodes': [],
    },
  )

  mocker.patch('seto.execute_setup_command')
  nfs_driver = mocker.patch('seto.NFSDriver')
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.20')
  mocker.patch(
    'sys.argv',
    [
      'seto',
      '--namespace',
      'test',
      'setup',
      '--storage-node',
      'nfs://admin@manager1',
      '--storage-replicas',
      'admin@replica1',
      '--clients',
      'admin@client1',
    ],
  )

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  contexts = nfs_driver.call_args.kwargs['contexts']
  assert contexts.context_for('manager1') == 'seto-manager1'


def test_setup_command_starts_from_an_unusable_configuration_file(mocker, tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  directory = tmp_path / '.seto'
  directory.mkdir()
  (directory / 'config.json').write_text('{ not json', encoding='utf-8')

  mocker.patch('seto.execute_setup_command')
  nfs_driver = mocker.patch('seto.NFSDriver')
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.20')
  mocker.patch(
    'sys.argv',
    [
      'seto',
      '--namespace',
      'test',
      'setup',
      '--storage-node',
      'nfs://admin@manager1',
      '--storage-replicas',
      'admin@replica1',
      '--clients',
      'admin@client1',
    ],
  )

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  contexts = nfs_driver.call_args.kwargs['contexts']
  assert contexts.config.storage_node is None


def test_commands_without_nfs_volumes_still_load_the_docker_context_mapping(
  mocker, tmp_path, monkeypatch
):
  monkeypatch.chdir(tmp_path)
  write_config_file(
    tmp_path,
    {
      'version': 2,
      'driver': 'nfs',
      'storage_node': {
        'hostname': 'manager1',
        'username': 'admin',
        'ip': '10.0.0.1',
        'local': False,
        'docker_context': 'seto-manager1',
      },
      'nodes': [
        {
          'hostname': 'client1',
          'username': 'admin',
          'ip': '10.0.0.2',
          'local': False,
          'docker_context': 'seto-client1',
        }
      ],
    },
  )

  stack_dir = tmp_path / 'app'
  stack_dir.mkdir()
  (stack_dir / 'app.yaml').write_text(
    'services:\n  web:\n    image: nginx\n', encoding='utf-8'
  )

  deploy = mocker.patch('seto.execute_deploy_command')
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.20')
  mocker.patch('sys.argv', ['seto', '--namespace', 'app', '--stack', 'app', 'deploy'])

  with pytest.raises(SystemExit) as exc_info:
    seto.run()

  assert exc_info.value.code == 0
  _args, driver = deploy.call_args.args
  assert driver.contexts.context_for('client1') == 'seto-client1'
  assert driver.contexts.manager_context() == 'seto-manager1'
