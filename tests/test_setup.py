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
# pylint: disable=import-error,invalid-name,protected-access
import json
from argparse import Namespace
from unittest.mock import MagicMock

import pytest
from seto.commands.setup import execute_setup_command
from seto.commands.setup import provision_docker_contexts
from seto.core.context import DockerContextError
from seto.core.context import DockerContextManager
from seto.core.context import DockerContextResolver
from seto.core.context import LOCAL_DOCKER_CONTEXT
from seto.core.context import NodeContext
from seto.core.context import SetoConfig
from seto.core.driver import Driver
from seto.core.network import global_networks_fingerprint
from seto.core.shell import Setting


class FakeDockerShell:
  """Docker CLI stub for a host that only has the built-in default context."""

  def __init__(self) -> None:
    self.contexts = ['default']
    self.calls: list[str] = []

  def run(self, command, **_kwargs):
    self.calls.append(command)

    if command.startswith('docker context ls'):
      return ''.join(f'{name}\n' for name in self.contexts)

    if command.startswith('docker context create'):
      self.contexts.append(command.split()[3])
      return ''

    if command.startswith('docker --context') and command.endswith(' info'):
      return 'Server Version: 29.0.0\n'

    raise AssertionError(f'unexpected Docker command: {command}')


def make_setting(hostname: str, *, local: bool = False) -> Setting:
  return Setting(
    hostname=hostname,
    username=None if local else 'admin',
    password=None,
    local=local,
    ip='127.0.0.1' if local else '10.0.0.10',
  )


def make_setup_args(tmp_path, *, clients=None, replicas=None) -> Namespace:
  key_path = tmp_path / 'id_rsa'
  key_path.write_text('key', encoding='utf-8')

  return Namespace(
    namespace='test',
    stack='app',
    ssh_key=str(key_path),
    force=False,
    image_prefix='',
    storage_replicas=[[make_setting(hostname)] for hostname in (replicas or ['storage1'])],
    clients=[[make_setting(hostname)] for hostname in (clients or ['client1', 'client2'])],
  )


def make_setup_driver(contexts, *, storage_node='manager1'):
  driver = MagicMock(spec=Driver)
  driver.slug = 'nfs'
  driver.storage_base_path = '/srv/nfs'
  driver.storage_node = MagicMock()
  driver.storage_node.setting = make_setting(storage_node)
  driver.contexts = contexts

  return driver


def test_setup_creates_a_docker_context_for_every_configured_node(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  contexts = contexts_factory()
  driver = make_setup_driver(contexts)
  args = make_setup_args(tmp_path)

  mocker.patch('seto.commands.setup.deploy_seto_stack')
  mocker.patch('seto.commands.setup.deploy_namespace_stack')

  execute_setup_command(args, driver)

  config = SetoConfig.load(str(tmp_path / '.seto' / 'config.json'))

  assert config.version == 2
  assert config.driver == 'nfs'
  assert config.storage_base_path == '/srv/nfs'
  assert config.storage_node.docker_context == 'seto-manager1'
  assert config.context_for('client1') == 'seto-client1'
  assert config.context_for('client2') == 'seto-client2'

  # Host-level provisioning happens before the Docker contexts are created
  driver.setup_storage_node.assert_called_once()
  driver.setup_nodes.assert_called_once()
  assert (tmp_path / '.seto' / '.gitignore').read_text(encoding='utf-8') == 'images/\n'


def test_setup_persists_the_default_context_of_the_current_node(mocker, tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)

  shell = FakeDockerShell()
  contexts = DockerContextResolver(SetoConfig(), manager=DockerContextManager(shell=shell))
  driver = make_setup_driver(contexts, storage_node='localhost')
  driver.storage_node.setting = make_setting('localhost', local=True)

  args = make_setup_args(tmp_path)
  args.clients = [[make_setting('localhost', local=True)]]

  mocker.patch('seto.commands.setup.deploy_seto_stack')
  mocker.patch('seto.commands.setup.deploy_namespace_stack')

  execute_setup_command(args, driver)

  config = SetoConfig.load(str(tmp_path / '.seto' / 'config.json'))

  # The current node relies on Docker's built-in default context; no
  # seto-localhost clone is created for it.
  assert config.storage_node.local is True
  assert config.storage_node.docker_context == LOCAL_DOCKER_CONTEXT
  assert config.context_for('localhost') == LOCAL_DOCKER_CONTEXT
  assert not any(command.startswith('docker context create') for command in shell.calls)


def test_setup_deploys_the_seto_stacks_after_contexts_are_ready(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  order: list[str] = []
  contexts = contexts_factory()
  contexts.manager.provision.side_effect = lambda node: (
    order.append(f'context:{node.hostname}') or node.derived_context
  )
  driver = make_setup_driver(contexts)
  driver.setup_storage_node.side_effect = lambda *args, **kwargs: order.append('host-provision')
  driver.setup_nodes.side_effect = lambda *args, **kwargs: order.append('client-provision')

  deploy_seto = mocker.patch(
    'seto.commands.setup.deploy_seto_stack', side_effect=lambda *a: order.append('deploy-seto')
  )
  deploy_namespace = mocker.patch(
    'seto.commands.setup.deploy_namespace_stack',
    side_effect=lambda *a: order.append('deploy-namespace'),
  )

  execute_setup_command(make_setup_args(tmp_path), driver)

  assert order == [
    'host-provision',
    'client-provision',
    'context:manager1',
    'context:client1',
    'context:client2',
    'deploy-seto',
    'deploy-namespace',
  ]
  deploy_seto.assert_called_once()
  deploy_namespace.assert_called_once()


def test_setup_marks_a_node_incomplete_when_its_context_fails(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  def _provision(node):
    if node.hostname == 'client2':
      raise DockerContextError('Docker context seto-client2 cannot reach its Docker daemon')

    return node.derived_context

  contexts = contexts_factory(provision=_provision)
  driver = make_setup_driver(contexts)

  deploy_seto = mocker.patch('seto.commands.setup.deploy_seto_stack')
  mocker.patch('seto.commands.setup.deploy_namespace_stack')

  with pytest.raises(DockerContextError, match='client2'):
    execute_setup_command(make_setup_args(tmp_path), driver)

  config = SetoConfig.load(str(tmp_path / '.seto' / 'config.json'))

  # Successfully verified contexts are retained; the failing node is recorded as
  # incomplete so it can never be used as a Compose target.
  assert config.context_for('client1') == 'seto-client1'
  assert config.node_for('client2') is not None
  assert config.context_for('client2') is None

  # The stacks are not deployed while the cluster is incomplete
  deploy_seto.assert_not_called()
  driver.terminate.assert_called_once()


def test_setup_reuses_the_context_name_persisted_by_a_previous_run(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  config_dir = tmp_path / '.seto'
  config_dir.mkdir()
  (config_dir / 'config.json').write_text(
    json.dumps(
      {
        'version': 2,
        'driver': 'nfs',
        'storage_node': {
          'hostname': 'manager1',
          'username': 'admin',
          'ip': '10.0.0.10',
          'local': False,
          'docker_context': 'seto-custom-manager',
        },
        'nodes': [
          {
            'hostname': 'client1',
            'username': 'admin',
            'ip': '10.0.0.10',
            'local': False,
            'docker_context': 'seto-custom-client1',
          }
        ],
      }
    ),
    encoding='utf-8',
  )

  provisioned: dict[str, str | None] = {}

  def _provision(node: NodeContext) -> str:
    provisioned[node.hostname] = node.docker_context

    return node.docker_context or node.derived_context

  contexts = contexts_factory(
    config=SetoConfig.load(str(config_dir / 'config.json')),
    provision=_provision,
  )
  driver = make_setup_driver(contexts)

  mocker.patch('seto.commands.setup.deploy_seto_stack')
  mocker.patch('seto.commands.setup.deploy_namespace_stack')

  execute_setup_command(make_setup_args(tmp_path), driver)

  assert provisioned['manager1'] == 'seto-custom-manager'
  assert provisioned['client1'] == 'seto-custom-client1'
  # New nodes fall back to the deterministic naming scheme
  assert provisioned['client2'] is None

  config = SetoConfig.load(str(config_dir / 'config.json'))
  assert config.context_for('manager1') == 'seto-custom-manager'
  assert config.context_for('client1') == 'seto-custom-client1'
  assert config.context_for('client2') == 'seto-client2'


def test_provision_docker_contexts_reports_every_failing_node(contexts_factory):
  def _provision(node):
    if node.hostname in ('client2', 'client3'):
      raise DockerContextError('unreachable')

    return node.derived_context

  contexts = contexts_factory(provision=_provision)
  driver = make_setup_driver(contexts)

  failures = provision_docker_contexts(
    driver,
    [make_setting('client1'), make_setting('client2'), make_setting('client3')],
  )

  assert failures == ['client2', 'client3']
  assert contexts.config.context_for('client1') == 'seto-client1'
  assert contexts.config.context_for('client2') is None
  assert contexts.config.context_for('client3') is None


def test_setup_persists_the_global_networks_fingerprint(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  contexts = contexts_factory()
  driver = make_setup_driver(contexts)
  args = make_setup_args(tmp_path)

  mocker.patch('seto.commands.setup.deploy_seto_stack')
  mocker.patch('seto.commands.setup.deploy_namespace_stack')

  execute_setup_command(args, driver)

  config = SetoConfig.load(str(tmp_path / '.seto' / 'config.json'))
  assert config.global_networks_fingerprint == global_networks_fingerprint('test')


def test_setup_rerun_skips_host_provisioning_and_provider_stack(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  contexts = contexts_factory()
  driver = make_setup_driver(contexts)
  args = make_setup_args(tmp_path)
  args.setup_fresh = False

  generate_keys = mocker.patch('seto.commands.setup.generate_ssh_keys')
  deploy_seto = mocker.patch('seto.commands.setup.deploy_seto_stack')
  deploy_namespace = mocker.patch('seto.commands.setup.deploy_namespace_stack')

  execute_setup_command(args, driver)

  # Host-level NFS provisioning never runs again once the configuration exists.
  generate_keys.assert_not_called()
  driver.setup_storage_node.assert_not_called()
  driver.setup_nodes.assert_not_called()
  deploy_seto.assert_not_called()
  # Docker contexts are still provisioned so a re-run repairs missing contexts.
  driver.contexts.manager.provision.assert_called()
  # The stored configuration predates fingerprints, so the namespace stack is
  # refreshed on the first re-run.
  driver.connect.assert_called_once()
  deploy_namespace.assert_called_once()


def test_setup_rerun_skips_namespace_deploy_when_networks_are_unchanged(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  contexts = contexts_factory()
  contexts.config.global_networks_fingerprint = global_networks_fingerprint('test')
  driver = make_setup_driver(contexts)
  args = make_setup_args(tmp_path)
  args.setup_fresh = False

  mocker.patch('seto.commands.setup.generate_ssh_keys')
  mocker.patch('seto.commands.setup.deploy_seto_stack')
  deploy_namespace = mocker.patch('seto.commands.setup.deploy_namespace_stack')

  execute_setup_command(args, driver)

  deploy_namespace.assert_not_called()
  # Nothing changed, so the NFS hosts are not contacted at all.
  driver.connect.assert_not_called()
  driver.apply_storage_node_changes.assert_not_called()

  config = SetoConfig.load(str(tmp_path / '.seto' / 'config.json'))
  assert config.global_networks_fingerprint == global_networks_fingerprint('test')


def test_setup_rerun_redeploys_namespace_when_networks_change(
  mocker, tmp_path, monkeypatch, contexts_factory
):
  monkeypatch.chdir(tmp_path)

  contexts = contexts_factory()
  contexts.config.global_networks_fingerprint = 'stale-fingerprint'
  driver = make_setup_driver(contexts)
  args = make_setup_args(tmp_path)
  args.setup_fresh = False

  mocker.patch('seto.commands.setup.generate_ssh_keys')
  mocker.patch('seto.commands.setup.deploy_seto_stack')
  deploy_namespace = mocker.patch('seto.commands.setup.deploy_namespace_stack')

  execute_setup_command(args, driver)

  deploy_namespace.assert_called_once()

  config = SetoConfig.load(str(tmp_path / '.seto' / 'config.json'))
  assert config.global_networks_fingerprint == global_networks_fingerprint('test')
