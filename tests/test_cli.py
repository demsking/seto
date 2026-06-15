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
# pylint: disable=import-error
import argparse
import socket

import pytest
from seto import nodes_type
from seto import storage_node_type
from seto import storage_replica_type


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
