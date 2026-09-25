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
# pylint: disable=import-error,invalid-name,redefined-outer-name
from unittest.mock import MagicMock

import pytest
from seto.core.context import DockerContextManager
from seto.core.context import DockerContextResolver
from seto.core.context import NodeContext
from seto.core.context import SetoConfig


def default_provision(node: NodeContext) -> str:
  """Mirror the real manager: reuse the persisted context name, else derive it."""
  return node.docker_context or node.derived_context


def default_client_factory(endpoint: str | None = None) -> MagicMock:
  return MagicMock()


def build_context_resolver(
  *,
  config: SetoConfig | None = None,
  nodes: dict[str, str] | None = None,
  storage_node: tuple[str, str] | None = None,
  endpoints: dict[str, str] | None = None,
  client_factory=None,
  key_file_path: str | None = None,
  provision=None,
) -> DockerContextResolver:
  """Build a resolver backed by a stubbed Docker CLI and SDK client factory."""
  if config is None:
    config = SetoConfig()

  if storage_node is not None:
    hostname, context = storage_node
    config.storage_node = NodeContext(hostname=hostname, username='admin', docker_context=context)

  for hostname, context in (nodes or {}).items():
    config.nodes.append(NodeContext(hostname=hostname, username='admin', docker_context=context))

  manager = MagicMock(spec=DockerContextManager)
  known_endpoints = dict(endpoints or {})
  manager.endpoint_of.side_effect = known_endpoints.get
  manager.provision.side_effect = provision or default_provision

  return DockerContextResolver(
    config,
    manager=manager,
    key_file_path=key_file_path,
    client_factory=client_factory,
  )


class ClientRecorder:
  """Records the endpoints of every Docker SDK client that is built."""

  def __init__(self) -> None:
    self.endpoints: list[str | None] = []
    self.clients: list[MagicMock] = []

  def __call__(self, endpoint=None):
    self.endpoints.append(endpoint)
    client = MagicMock()
    self.clients.append(client)

    return client


@pytest.fixture
def contexts_factory():
  return build_context_resolver


@pytest.fixture
def client_recorder():
  return ClientRecorder()
