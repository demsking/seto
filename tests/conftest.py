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
import json
import os
import threading
from unittest.mock import MagicMock
from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest
from seto.core.context import DockerContextManager
from seto.core.context import DockerContextResolver
from seto.core.context import LOCAL_DOCKER_CONTEXT
from seto.core.context import NodeContext
from seto.core.context import SetoConfig


def default_provision(node: NodeContext) -> str:
  """Mirror the real manager: the local node uses the default context."""
  if node.local:
    return LOCAL_DOCKER_CONTEXT

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


PROVIDER_ENDPOINT_ENV = 'SETO_TRAEFIK_HTTP_ENDPOINT'


class ProviderRecorder:
  """In-memory Traefik HTTP provider the CLI reaches through ``urlopen``.

  Publishing a route performs a real PUT to the provider port, which would hit
  the network (and wait for a timeout) in a test. This recorder answers every
  provider request locally, exactly like the provider served by
  ``seto.core.provider_server``, and keeps the state observable.

  Every provider call re-enters this module (``urllib_request.urlopen`` is
  patched module-wide), so the helpers below use pure dict operations only: a
  re-entrant ``json.dumps`` would see the patched ``json`` global.
  """

  def __init__(self, endpoint: str = 'http://seto-provider:8080/traefik') -> None:
    self.endpoint = endpoint
    self.http: dict[str, dict] = {'routers': {}, 'services': {}}
    self.routes: dict[str, dict] = {}
    self.requests: list = []
    # Raw diagnostic trail: re-entrant calls show up here even if they are
    # answered before being recorded as requests.
    self.trace: list = []
    self.fail = False
    # Requests forwarded to the real urllib instead of being answered here.
    self.passthrough: list = []
    self.passthrough_to: str | None = None
    self._real_urlopen = urllib_request.urlopen

  def urlopen(self, request, timeout=5):
    full_url = request if isinstance(request, str) else request.full_url

    if self.fail:
      raise urllib_error.URLError(f'provider unreachable: {full_url}')

    is_request = not isinstance(request, str)

    if self.passthrough_to and not full_url.startswith(self.passthrough_to):
      # Loopback traffic to the real in-process provider server is not the
      # Traefik provider: forward it to the real urllib implementation.
      self.passthrough.append((request.get_method() if is_request else 'GET', full_url))

      with self._real_urlopen(request, timeout=timeout) as response:
        return RecordingResponse(response.getcode(), body=response.read())

    if not is_request:
      raise AssertionError(f'unexpected urlopen(str): {full_url}')

    # Candidate endpoints also include the localhost fallbacks of the provider,
    # so the route id is whatever follows the /traefik suffix of any of them.
    suffix = full_url.rsplit('/traefik', 1)[-1] if '/traefik' in full_url else ''
    path = suffix.strip('/')
    method = request.get_method()
    self.trace.append((threading.current_thread().name, method, full_url))
    self.requests.append((method, path))

    if method == 'PUT':
      payload = self._decode(request.data)

      self._merge_http(payload.get('http'))

      registry = payload.get('_seto_registry') if isinstance(payload, dict) else None
      records = registry.get('routes') if isinstance(registry, dict) else None

      if isinstance(records, dict):
        for route_id, record in records.items():
          if isinstance(record, dict):
            self.routes[route_id] = record

      if path:
        self.routes[path] = {
          key: value for key, value in payload.items() if key in {'http', 'route_id', 'probe'}
        }

      return RecordingResponse(204)

    if method == 'GET':
      return RecordingResponse(
        200, {'http': self.http, '_seto_registry': {'routes': dict(self.routes)}}
      )

    if method == 'DELETE':
      self.routes.pop(path, None)

      return RecordingResponse(204)

    return RecordingResponse(405)

  def _merge_http(self, http_config) -> None:
    if not isinstance(http_config, dict):
      return

    for section in ('routers', 'services'):
      entries = http_config.get(section)

      if isinstance(entries, dict):
        self.http[section].update(entries)

  def _decode(self, data) -> dict:
    """Decode a JSON request body without touching the patched ``json`` name."""
    if not data:
      return {}

    try:
      decoded = json.loads(data.decode('utf-8'))
    except ValueError:
      return {}

    return decoded if isinstance(decoded, dict) else {}

  def registered_ids(self) -> list:
    return [path for method, path in self.requests if method == 'PUT' and path]


class RecordingResponse:
  def __init__(self, status: int = 204, payload=None, body: bytes | None = None) -> None:
    self._status = status
    self._payload = payload

    if body is not None:
      self.body = body
    elif payload is not None:
      self.body = json.dumps(payload).encode('utf-8')
    else:
      self.body = b''

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, traceback) -> bool:
    return False

  def getcode(self) -> int:
    return self._status

  def read(self) -> bytes:
    return self.body


@pytest.fixture(autouse=True)
def provider_recorder(mocker):
  """Keep the whole suite offline: no test may reach the Traefik provider."""
  mocker.patch.dict(os.environ, {}, clear=False)
  os.environ.pop(PROVIDER_ENDPOINT_ENV, None)

  recorder = ProviderRecorder()
  mocker.patch(
    'seto.core.traefik.urllib_request.urlopen',
    side_effect=recorder.urlopen,
  )
  mocker.patch(
    'seto.core.provider_server.urllib_request.urlopen',
    side_effect=recorder.urlopen,
  )

  return recorder


@pytest.fixture
def contexts_factory():
  return build_context_resolver


@pytest.fixture
def client_recorder():
  return ClientRecorder()
