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
# pylint: disable=import-error,invalid-name,protected-access,unused-import
import json
from unittest.mock import MagicMock

import pytest
from seto.core.context import build_docker_client
from seto.core.context import docker_cli
from seto.core.context import docker_context_name
from seto.core.context import docker_endpoint
from seto.core.context import DockerContextConflictError
from seto.core.context import DockerContextError
from seto.core.context import DockerContextManager
from seto.core.context import DockerContextResolver
from seto.core.context import DockerContextUnavailableError
from seto.core.context import LOCAL_DOCKER_ENDPOINT
from seto.core.context import NodeContext
from seto.core.context import NodeContextMissingError
from seto.core.context import SetoConfig
from seto.core.context import SetoConfigError
from seto.core.shell import Setting
from seto.core.shell import Shell
from seto.shells.local import LocalShell
from seto.shells.remote import RemoteShell


def make_manager(outputs: dict[str, str], *, calls: list[str] | None = None):
  """Build a DockerContextManager whose Docker CLI calls are stubbed."""
  recorded: list[str] = calls if calls is not None else []
  shell = MagicMock(spec=Shell)

  def _run(command, **kwargs):
    recorded.append(command)

    for prefix, output in outputs.items():
      if command.startswith(prefix):
        if isinstance(output, Exception):
          raise output

        return output

    return ''

  shell.run.side_effect = _run

  return DockerContextManager(shell=shell), recorded


CONTEXT_INSPECT_OUTPUT = json.dumps(
  [
    {
      'Name': 'seto-client1',
      'Endpoints': {'docker': {'Host': 'ssh://admin@client1', 'SkipTLSVerify': False}},
    }
  ]
)


# ---------------------------------------------------------------------------
# Naming and endpoints
# ---------------------------------------------------------------------------


def test_docker_context_name_is_deterministic_and_prefixed():
  assert docker_context_name('client1') == 'seto-client1'
  assert docker_context_name('client1') == docker_context_name('CLIENT1')
  assert docker_context_name('worker-node-1.local') == 'seto-worker-node-1.local'


def test_docker_context_name_is_collision_safe_for_unsafe_hostnames():
  first = docker_context_name('weird host!')
  second = docker_context_name('weird-host-')

  assert first.startswith('seto-weird-host-')
  assert first != second
  assert first == docker_context_name('weird host!')
  assert docker_context_name('a.b') != docker_context_name('a-b')


def test_docker_context_name_rejects_empty_hostname():
  with pytest.raises(DockerContextError, match='empty hostname'):
    docker_context_name('   ')


def test_docker_endpoint_uses_native_ssh_transport_for_remote_nodes():
  assert docker_endpoint('client1', 'admin') == 'ssh://admin@client1'
  assert docker_endpoint('client1', 'admin', local=False) == 'ssh://admin@client1'


def test_docker_endpoint_uses_local_docker_socket_for_local_nodes(mocker):
  mocker.patch.dict('os.environ', {}, clear=False)
  assert docker_endpoint('localhost', None, local=True) == LOCAL_DOCKER_ENDPOINT

  mocker.patch.dict('os.environ', {'DOCKER_HOST': 'unix:///run/user/1000/docker.sock'})
  assert docker_endpoint('localhost', None, local=True) == 'unix:///run/user/1000/docker.sock'


def test_docker_endpoint_rejects_malformed_node_configuration():
  with pytest.raises(DockerContextError, match='Invalid node hostname'):
    docker_endpoint('bad host', 'admin')

  with pytest.raises(DockerContextError, match='Invalid SSH username'):
    docker_endpoint('client1', 'admin; rm -rf /')

  with pytest.raises(DockerContextError, match='Missing SSH username'):
    docker_endpoint('client1', None)


def test_docker_endpoint_inserts_no_credentials():
  endpoint = docker_endpoint('client1', 'admin')

  assert 'password' not in endpoint
  assert endpoint.count('@') == 1


def test_docker_cli_always_targets_an_explicit_context():
  assert docker_cli('seto-client1', 'compose up') == 'docker --context seto-client1 compose up'
  assert docker_cli(None, 'compose up') == 'docker compose up'
  assert docker_cli('seto-client1', 'compose up') != 'docker compose up'


# ---------------------------------------------------------------------------
# Configuration persistence
# ---------------------------------------------------------------------------


def test_seto_config_round_trips_node_to_context_mapping(tmp_path):
  path = str(tmp_path / 'config.json')
  config = SetoConfig(driver='nfs', storage_base_path='/srv/nfs')
  config.storage_node = NodeContext(
    hostname='manager1', username='admin', ip='10.0.0.1', docker_context='seto-manager1'
  )
  config.upsert_node(
    NodeContext(hostname='client1', username='admin', ip='10.0.0.2', docker_context='seto-client1')
  )
  config.save(path)

  reloaded = SetoConfig.load(path)

  assert reloaded is not None
  assert reloaded.version == 2
  assert reloaded.storage_base_path == '/srv/nfs'
  assert reloaded.context_for('client1') == 'seto-client1'
  assert reloaded.context_for('manager1') == 'seto-manager1'
  assert reloaded.context_for('unknown') is None
  assert reloaded.node_for('CLIENT1').username == 'admin'


def test_seto_config_load_returns_none_when_missing(tmp_path):
  assert SetoConfig.load(str(tmp_path / 'missing.json')) is None


def test_seto_config_rejects_corrupted_file(tmp_path):
  path = tmp_path / 'config.json'
  path.write_text('{ not json', encoding='utf-8')

  with pytest.raises(SetoConfigError):
    SetoConfig.load(str(path))


def test_seto_config_reads_legacy_configuration_without_contexts(tmp_path):
  path = tmp_path / 'config.json'
  path.write_text(
    json.dumps(
      {
        'driver': 'nfs',
        'storage_base_path': '/mnt/nfs',
        'manager': {
          'hostname': 'localhost',
          'username': 'ndemnou',
          'ip': '127.0.0.1',
          'local': True,
        },
      }
    ),
    encoding='utf-8',
  )

  config = SetoConfig.load(str(path))

  assert config is not None
  # Legacy configurations are backward compatible but register no context:
  # Docker access is only re-established by re-running `seto setup`.
  assert config.storage_node.hostname == 'localhost'
  assert config.storage_node.docker_context is None
  assert config.context_for('localhost') is None


def test_seto_config_rejects_invalid_nodes_entry(tmp_path):
  path = tmp_path / 'config.json'
  path.write_text(json.dumps({'nodes': {'client1': 'seto-client1'}}), encoding='utf-8')

  with pytest.raises(SetoConfigError, match='must be a list'):
    SetoConfig.load(str(path))


def test_seto_config_upsert_replaces_an_existing_node():
  config = SetoConfig()
  config.upsert_node(NodeContext(hostname='client1', docker_context=None))
  config.upsert_node(NodeContext(hostname='client1', docker_context='seto-client1'))

  assert len(config.nodes) == 1
  assert config.context_for('client1') == 'seto-client1'


# ---------------------------------------------------------------------------
# Context creation / verification
# ---------------------------------------------------------------------------


def test_docker_context_manager_reports_a_missing_docker_cli():
  manager, _calls = make_manager({'docker context ls': Exception('docker: command not found')})

  with pytest.raises(DockerContextError, match='Unable to run the Docker CLI'):
    manager.list_contexts()


def test_docker_context_manager_creates_a_missing_context():
  calls: list[str] = []
  manager, _calls = make_manager(
    {'docker context ls': 'default\n', 'docker context inspect': CONTEXT_INSPECT_OUTPUT},
    calls=calls,
  )

  node = NodeContext(hostname='client1', username='admin')
  context, created = manager.ensure(node)

  assert context == 'seto-client1'
  assert created is True
  assert 'docker context create seto-client1 --docker host=ssh://admin@client1' in calls


def test_docker_context_manager_reuses_an_existing_expected_context():
  calls: list[str] = []
  manager, _calls = make_manager(
    {
      'docker context ls': 'default\nseto-client1\n',
      'docker context inspect': CONTEXT_INSPECT_OUTPUT,
    },
    calls=calls,
  )

  node = NodeContext(hostname='client1', username='admin', docker_context='seto-client1')
  context, created = manager.ensure(node)

  assert context == 'seto-client1'
  assert created is False
  assert not any(command.startswith('docker context create') for command in calls)
  assert not any(command.startswith('docker context update') for command in calls)


def test_docker_context_manager_detects_conflicting_context():
  calls: list[str] = []
  manager, _calls = make_manager(
    {
      'docker context ls': 'seto-client1\n',
      'docker context inspect': json.dumps(
        [{'Name': 'seto-client1', 'Endpoints': {'docker': {'Host': 'ssh://root@other-node'}}}]
      ),
    },
    calls=calls,
  )

  node = NodeContext(hostname='client1', username='admin')

  with pytest.raises(DockerContextConflictError, match='already exists'):
    manager.ensure(node)

  assert not any(command.startswith('docker context create') for command in calls)
  assert not any(command.startswith('docker context update') for command in calls)


def test_docker_context_manager_verifies_context_reachability():
  calls: list[str] = []
  manager, _calls = make_manager(
    {
      'docker context ls': 'seto-client1\n',
      'docker context inspect': CONTEXT_INSPECT_OUTPUT,
      'docker --context seto-client1 info': 'Server Version: 29.0.0\n',
    },
    calls=calls,
  )

  node = NodeContext(hostname='client1', username='admin')
  context = manager.provision(node)

  assert context == 'seto-client1'
  assert 'docker --context seto-client1 info' in calls


def test_docker_context_manager_refreshes_a_stale_context():
  calls: list[str] = []
  attempts = {'count': 0}
  manager, _calls = make_manager(
    {
      'docker context ls': 'seto-client1\n',
      'docker context inspect': CONTEXT_INSPECT_OUTPUT,
    },
    calls=calls,
  )

  def _verify(context):
    attempts['count'] += 1

    if attempts['count'] == 1:
      raise DockerContextUnavailableError('stale')

  manager.verify = _verify

  node = NodeContext(hostname='client1', username='admin')
  manager.provision(node)

  assert attempts['count'] == 2
  assert 'docker context update seto-client1 --docker host=ssh://admin@client1' in calls


def test_docker_context_manager_fails_when_the_daemon_is_unreachable():
  calls: list[str] = []
  manager, _calls = make_manager(
    {
      'docker context ls': 'seto-client1\n',
      'docker context inspect': CONTEXT_INSPECT_OUTPUT,
      'docker --context seto-client1 info': Exception('permission denied while trying to connect'),
      'docker context update': '',
    },
    calls=calls,
  )

  node = NodeContext(hostname='client1', username='admin')

  with pytest.raises(DockerContextUnavailableError, match='cannot reach its Docker daemon'):
    manager.provision(node)


def test_docker_context_manager_fails_when_ssh_authentication_fails():
  manager, _calls = make_manager(
    {
      'docker context ls': 'seto-client1\n',
      'docker context inspect': CONTEXT_INSPECT_OUTPUT,
      'docker --context seto-client1 info': Exception('Permission denied (publickey)'),
    }
  )

  node = NodeContext(hostname='client1', username='admin')

  with pytest.raises(DockerContextUnavailableError, match='SSH transport'):
    manager.provision(node)


def test_docker_context_manager_rejects_malformed_context_names():
  calls: list[str] = []
  manager, _calls = make_manager({'docker context ls': 'default\n'}, calls=calls)

  node = NodeContext(hostname='client1', username='admin')
  node.docker_context = 'seto-client1; touch /tmp/pwned'

  with pytest.raises(DockerContextError, match='Invalid Docker context name'):
    manager.ensure(node)

  assert not calls


def test_docker_context_manager_builds_a_safe_context_creation_command():
  calls: list[str] = []
  manager, _calls = make_manager({'docker context ls': 'default\n'}, calls=calls)

  manager.ensure(NodeContext(hostname='client1', username='admin'))

  assert calls == [
    "docker context ls --format '{{.Name}}'",
    'docker context create seto-client1 --docker host=ssh://admin@client1',
  ]


def test_docker_context_create_command_never_contains_credentials():
  calls: list[str] = []
  manager, _calls = make_manager({'docker context ls': 'default\n'}, calls=calls)

  manager.ensure(NodeContext(hostname='client1', username='admin'))

  create_command = next(c for c in calls if c.startswith('docker context create'))

  assert 'password' not in create_command
  assert ':' not in create_command.replace('ssh://', '')


# ---------------------------------------------------------------------------
# Docker SDK clients
# ---------------------------------------------------------------------------


def test_build_docker_client_uses_the_context_endpoint(mocker):
  client_class = mocker.patch('seto.core.context.DockerClient')

  build_docker_client(None)
  client_class.from_env.assert_called_once_with()

  build_docker_client('unix:///var/run/docker.sock')
  client_class.assert_called_once_with(base_url='unix:///var/run/docker.sock')


def test_build_docker_client_uses_the_ssh_transport_for_ssh_contexts(mocker):
  client_class = mocker.patch('seto.core.context.DockerClient')

  build_docker_client('ssh://admin@client1')

  client_class.assert_called_once_with(base_url='ssh://admin@client1', use_ssh_client=True)


def test_resolver_client_wraps_sdk_connection_failures():
  def _factory(_endpoint):
    raise RuntimeError('connection refused')

  resolver = DockerContextResolver(
    _config_with_nodes({'client1': 'seto-client1'}),
    manager=MagicMock(spec=DockerContextManager),
    client_factory=_factory,
  )

  with pytest.raises(DockerContextUnavailableError, match='connection refused'):
    resolver.node_client('client1')


def test_resolver_builds_the_client_of_the_node_context(client_recorder):
  resolver = DockerContextResolver(
    _config_with_nodes({'client1': 'seto-client1'}),
    manager=MagicMock(spec=DockerContextManager),
    client_factory=client_recorder,
  )
  resolver.manager.endpoint_of.side_effect = {'seto-client1': 'ssh://admin@client1'}.get

  client = resolver.node_client('client1')

  assert client_recorder.endpoints == ['ssh://admin@client1']
  assert client is client_recorder.clients[0]

  # Clients are cached per context and closed on shutdown
  assert resolver.node_client('client1') is client

  resolver.close()
  client.close.assert_called_once_with()


def test_resolver_manager_client_falls_back_to_the_local_daemon(client_recorder):
  resolver = DockerContextResolver(
    SetoConfig(),
    manager=MagicMock(spec=DockerContextManager),
    client_factory=client_recorder,
  )

  resolver.manager_client()

  assert client_recorder.endpoints == [None]


# ---------------------------------------------------------------------------
# Node / context resolution
# ---------------------------------------------------------------------------


def _config_with_nodes(nodes: dict[str, str]) -> SetoConfig:
  config = SetoConfig()

  for hostname, context in nodes.items():
    config.nodes.append(
      NodeContext(hostname=hostname, username='admin', ip='10.0.0.2', docker_context=context)
    )

  return config


def test_resolver_maps_nodes_to_their_docker_context():
  resolver = DockerContextResolver(_config_with_nodes({'client1': 'seto-client1'}))

  assert resolver.context_for('client1') == 'seto-client1'
  assert resolver.require_context('client1') == 'seto-client1'
  assert resolver.node_for('client1').username == 'admin'


def test_resolver_reports_unregistered_nodes():
  resolver = DockerContextResolver(_config_with_nodes({'client1': 'seto-client1'}))

  assert resolver.context_for('client2') is None

  with pytest.raises(NodeContextMissingError, match='seto setup'):
    resolver.require_context('client2')

  with pytest.raises(NodeContextMissingError, match='no Docker context registered'):
    resolver.node_cli('client2', 'compose up')

  with pytest.raises(NodeContextMissingError, match='not registered'):
    resolver.node_shell('client2')


def test_resolver_never_targets_the_current_context():
  resolver = DockerContextResolver(_config_with_nodes({'client1': 'seto-client1'}))

  assert (
    resolver.node_cli('client1', 'compose -f - up -d')
    == 'docker --context seto-client1 compose -f - up -d'
  )
  assert resolver.local_cli('compose build') == 'docker --context default compose build'
  assert resolver.manager_cli('stack deploy demo') == 'docker stack deploy demo'
  assert resolver.manager_cli('stack ps demo') == 'docker stack ps demo'


def test_resolver_manager_cli_uses_the_manager_context():
  config = _config_with_nodes({'client1': 'seto-client1'})
  config.storage_node = NodeContext(hostname='manager1', username='admin', docker_context='seto-m1')
  resolver = DockerContextResolver(config)

  assert resolver.manager_context() == 'seto-m1'
  assert resolver.manager_cli('stack deploy demo') == 'docker --context seto-m1 stack deploy demo'


def test_resolver_node_shell_uses_ssh_for_remote_nodes_and_local_for_local_nodes(mocker):
  config = SetoConfig()
  config.nodes.append(NodeContext(hostname='client1', username='admin', docker_context='seto-c1'))
  config.nodes.append(
    NodeContext(hostname='laptop', username='ndemnou', local=True, docker_context='seto-laptop')
  )
  resolver = DockerContextResolver(config, key_file_path='~/.ssh/id_rsa')

  remote = resolver.node_shell('client1')
  assert isinstance(remote, RemoteShell)
  assert remote.key_file_path == '~/.ssh/id_rsa'
  assert remote.username == 'admin'

  local = resolver.node_shell('laptop')
  assert isinstance(local, LocalShell)
  assert local.setting.local is True


def test_node_context_exposes_its_endpoint_and_setting():
  node = NodeContext(hostname='client1', username='admin', ip='10.0.0.2')

  assert node.derived_context == 'seto-client1'
  assert node.endpoint == 'ssh://admin@client1'
  assert node.to_setting() == Setting(
    hostname='client1', username='admin', password=None, local=False, ip='10.0.0.2'
  )
