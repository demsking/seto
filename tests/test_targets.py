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
# pylint: disable=import-error,invalid-name,protected-access,no-member
from argparse import Namespace
from unittest.mock import MagicMock

import pytest
from seto.commands.resolve import resolve
from seto.core.context import DockerContextResolver
from seto.core.context import NodeContext
from seto.core.context import SetoConfig
from seto.core.driver import Driver
from seto.core.shell import Shell
from seto.core.targets import ComposeTargetResolver
from seto.core.targets import constraint_groups
from seto.core.targets import match_node_constraint


def swarm_node(hostname, *, role='worker', labels=None):
  node = MagicMock()
  node.attrs = {
    'Description': {'Hostname': hostname},
    'Status': {'Addr': '10.42.0.5'},
    'Spec': {'Role': role, 'Labels': dict(labels or {})},
  }

  return node


def make_contexts(nodes: dict[str, str]):
  config = SetoConfig()

  for hostname, context in nodes.items():
    config.nodes.append(NodeContext(hostname=hostname, username='admin', docker_context=context))

  return DockerContextResolver(config)


def test_constraint_groups_normalizes_flat_and_nested_placement():
  assert constraint_groups(['host.device.gpu == true']) == [['host.device.gpu == true']]
  assert constraint_groups([['host.role == worker', 'host.device.gpu == true']]) == [
    ['host.role == worker', 'host.device.gpu == true']
  ]
  assert not constraint_groups([])


def test_match_node_constraint_supports_device_role_name_and_labels():
  node = swarm_node('worker-node-1', labels={'gpu': 'true', 'device': 'zigbee'})

  assert match_node_constraint(node, 'host.device.gpu', 'true') is True
  assert match_node_constraint(node, 'host.device.gpu', 'false') is False
  assert match_node_constraint(node, 'host.role', 'worker') is True
  assert match_node_constraint(node, 'host.role', 'manager') is False
  assert match_node_constraint(node, 'host.name', 'worker-node-1') is True
  assert match_node_constraint(node, 'node.labels.gpu', 'true') is True
  assert match_node_constraint(node, 'node.hostname', 'worker-node-1') is True


def test_match_node_constraint_defers_supported_devices_to_physical_verification():
  node = swarm_node('worker-node-1')

  assert match_node_constraint(node, 'host.device.gpu', 'true') is True
  assert match_node_constraint(node, 'host.device.microphone', 'true') is False


def test_target_resolver_requires_placement_constraints():
  resolver = ComposeTargetResolver(placement=[], nodes=[], contexts=make_contexts({}))

  with pytest.raises(ValueError, match='Missing required x-placement field'):
    resolver.resolve()


def test_target_resolver_fails_when_no_node_matches():
  resolver = ComposeTargetResolver(
    placement=['host.name == unknown'],
    nodes=[swarm_node('worker-node-1')],
    contexts=make_contexts({'worker-node-1': 'seto-worker-node-1'}),
  )

  with pytest.raises(ValueError, match='Unable to found node'):
    resolver.resolve()


def test_target_resolver_selects_only_context_backed_nodes():
  resolver = ComposeTargetResolver(
    placement=['host.role == worker'],
    nodes=[swarm_node('node1'), swarm_node('node2')],
    contexts=make_contexts({'node1': 'seto-node1'}),
  )

  targets = resolver.resolve()

  # node2 matches the hardware constraint but has no Docker context
  assert targets.matched == ['node1', 'node2']
  assert targets.deployable == ['node1']


def test_target_resolver_treats_the_current_node_as_context_backed(mocker):
  mocker.patch('seto.core.context.socket.gethostname', return_value='kama')

  resolver = ComposeTargetResolver(
    placement=['host.name == kama'],
    nodes=[swarm_node('kama')],
    contexts=make_contexts({}),
  )

  targets = resolver.resolve()

  # The machine running Ṣeto always resolves through the built-in default
  # context, even though it has no persisted entry in .seto/config.json.
  assert targets.matched == ['kama']
  assert targets.deployable == ['kama']


def test_target_resolver_verifies_devices_over_administrative_ssh(mocker):
  contexts = make_contexts({'node1': 'seto-node1', 'node2': 'seto-node2'})
  failing_shell = MagicMock(spec=Shell)
  failing_shell.run.side_effect = Exception('Zigbee device not found')
  good_shell = MagicMock(spec=Shell)
  mocker.patch.object(
    contexts,
    'node_shell',
    side_effect=lambda hostname: failing_shell if hostname == 'node1' else good_shell,
  )

  resolver = ComposeTargetResolver(
    placement=['host.device.zigbee == true'],
    nodes=[swarm_node('node1'), swarm_node('node2')],
    contexts=contexts,
  )

  targets = resolver.resolve()

  assert targets.matched == ['node1', 'node2']
  assert targets.deployable == ['node2']
  good_shell.connect.assert_called_once()
  good_shell.close.assert_called_once()
  assert 'ttyUSB0' in good_shell.run.call_args.args[0]


def test_resolve_falls_back_to_service_level_placement_constraints(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)

  driver = MagicMock(spec=Driver)
  driver.stack_id = 'stack'
  driver.resolve_compose_volume.return_value = {}

  compose_yaml = tmp_path / 'compose.yaml'
  compose_yaml.write_text(
    """
x-mode: compose
services:
  gpu:
    image: nginx:latest
    deploy:
      placement:
        constraints:
          - host.name == worker-node-1
""",
    encoding='utf-8',
  )

  mocker.patch('seto.core.parser.load_env_file')

  resolved = resolve(
    Namespace(
      namespace='test',
      stack=str(tmp_path),
      image_prefix='',
      default_volume_mount_mode='rw',
      default_nfs_options='rw,hard,noatime,nodiratime,nfsvers=4',
    ),
    driver,
  )

  assert resolved['x-placement'] == [['host.name == worker-node-1']]
