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
# pylint: disable=import-error,protected-access,invalid-name,unused-import
from unittest.mock import MagicMock

from seto.core.docker import DockerCompose
from seto.core.driver import Driver


def test_docker_compose_node_hostname_replacement(mocker):
  # Mock resolve_hostname and socket.gethostname
  mocker.patch('seto.core.shell.resolve_hostname', return_value='127.0.0.1')
  mocker.patch('socket.gethostname', return_value='local-host')

  # Create driver mock
  driver = MagicMock(spec=Driver)
  driver.stack_id = 'mystack'
  driver.debug = True
  driver.storage_node = False
  driver.host = MagicMock()

  # Create client mock
  client = MagicMock()
  node1 = MagicMock()
  node1.attrs = {
    'Description': {'Hostname': 'node1'},
    'Spec': {'Role': 'worker', 'Labels': {}},
  }
  client.nodes.list.return_value = [node1]

  # Configuration using template placeholder
  config = {
    'x-placement': ['node.hostname==node1'],
    'services': {
      'web': {
        'image': 'nginx',
        'hostname': '{{ .Node.Hostname }}',
        'environment': {
          'HOST': '{{.Node.Hostname}}',
        },
      }
    },
  }

  compose = DockerCompose(config=config, driver=driver, client=client)

  # Verify nodenames
  assert compose.nodenames == ['node1']

  # Mock _remote_node_run and shell.run
  mocker.patch.object(compose, '_remote_node_run')
  mocker.patch.object(compose.shell, 'run')

  # Run info()
  compose.info()

  # Verify that _remote_node_run was called with replaced hostname in the input
  compose._remote_node_run.assert_called_once()
  _args, kwargs = compose._remote_node_run.call_args
  assert kwargs['hostname'] == 'node1'
  assert 'node1' in kwargs['input']
  assert '{{ .Node.Hostname }}' not in kwargs['input']
  assert '{{.Node.Hostname}}' not in kwargs['input']

  # Reset mock and run deploy()
  compose._remote_node_run.reset_mock()
  compose.deploy()

  # Verify that _remote_node_run was called with replaced hostname in the input
  compose._remote_node_run.assert_called_once()

  assert kwargs['hostname'] == 'node1'
  assert 'node1' in kwargs['input']
  assert '{{ .Node.Hostname }}' not in kwargs['input']
  assert '{{.Node.Hostname}}' not in kwargs['input']
