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
# pylint: disable=import-error,protected-access,invalid-name,no-member,redefined-outer-name
import json
import threading
import time
from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest
from seto.core.provider_server import build_server
from seto.core.provider_server import DEFAULT_TTL_SECONDS
from seto.core.provider_server import group_routes
from seto.core.provider_server import LISTEN_ADDRESS
from seto.core.provider_server import normalize_route
from seto.core.provider_server import probe_route
from seto.core.provider_server import PROBE_TIMEOUT_SECONDS
from seto.core.provider_server import resolve_ttl
from seto.core.provider_server import route_server_urls
from seto.core.provider_server import RouteRegistry
from seto.core.provider_server import start_reaper
from seto.core.provider_server import sweep_interval

FULL_ROUTE_ID = 'oremi_iot_mali-zigbee2mqtt'


def make_route(service_name: str = 'zigbee2mqtt', *, hostname: str = '10.42.0.5') -> dict:
  return {
    'http': {
      'routers': {service_name: {'rule': f'Host(`{service_name}.example.com`)'}},
      'services': {
        service_name: {
          'loadBalancer': {'servers': [{'url': f'http://{hostname}:32771'}]},
        }
      },
    },
    'route_id': service_name,
  }


class RecordingResponse:
  def __init__(self, status: int = 200) -> None:
    self._status = status

  def __enter__(self) -> 'RecordingResponse':
    return self

  def __exit__(self, exc_type, exc, traceback) -> bool:
    return False

  def getcode(self) -> int:
    return self._status

  def read(self) -> bytes:
    return b''


# --- constants ----------------------------------------------------------------


def test_provider_constants() -> None:
  assert DEFAULT_TTL_SECONDS == 5
  assert PROBE_TIMEOUT_SECONDS == 2
  assert LISTEN_ADDRESS == ('0.0.0.0', 8080)


@pytest.mark.parametrize(
  ('argv', 'expected'),
  [
    ([], DEFAULT_TTL_SECONDS),
    (['9'], 9),
    (['0'], 1),
    (['-4'], 1),
    (['abc'], DEFAULT_TTL_SECONDS),
  ],
)
def test_resolve_ttl(argv: list, expected: int) -> None:
  assert resolve_ttl(argv) == expected


@pytest.mark.parametrize(('ttl', 'expected'), [(5, 1.0), (10, 1.0), (2, 0.5), (1, 0.5)])
def test_sweep_interval_stays_well_within_the_ttl(ttl: int, expected: float) -> None:
  assert sweep_interval(ttl) == expected


# --- route helpers ------------------------------------------------------------


def test_route_server_urls_reads_load_balancer_servers() -> None:
  assert route_server_urls(make_route()) == ['http://10.42.0.5:32771']


@pytest.mark.parametrize('route', [{}, {'http': {}}, {'http': {'services': {}}}])
def test_route_server_urls_without_backends(route: dict) -> None:
  assert not route_server_urls(route)


def test_probe_route_answers_true_for_any_http_status(mocker) -> None:
  probed: list = []

  def _urlopen(request, timeout=PROBE_TIMEOUT_SECONDS):
    probed.append((request.get_method(), request.full_url, timeout))
    return RecordingResponse(404)

  mocker.patch(
    'seto.core.provider_server.urllib_request.urlopen',
    side_effect=_urlopen,
  )

  assert probe_route(make_route()) is True
  assert probed == [('HEAD', 'http://10.42.0.5:32771', PROBE_TIMEOUT_SECONDS)]


def test_probe_route_accepts_http_error(mocker) -> None:
  def _urlopen(_request, timeout=PROBE_TIMEOUT_SECONDS):
    raise urllib_error.HTTPError(
      url='http://10.42.0.5:32771',
      code=503,
      msg='Service Unavailable',
      hdrs=None,
      fp=None,
    )

  mocker.patch('seto.core.provider_server.urllib_request.urlopen', side_effect=_urlopen)

  assert probe_route(make_route()) is True


def test_probe_route_is_false_when_every_backend_fails(mocker) -> None:
  def _urlopen(_request, timeout=PROBE_TIMEOUT_SECONDS):
    raise urllib_error.URLError('connection refused')

  mocker.patch('seto.core.provider_server.urllib_request.urlopen', side_effect=_urlopen)

  assert probe_route(make_route()) is False


def test_probe_route_is_false_without_urls(mocker) -> None:
  urlopen = mocker.patch('seto.core.provider_server.urllib_request.urlopen')

  assert probe_route({'http': {'routers': {}, 'services': {}}}) is False
  urlopen.assert_not_called()


def test_normalize_route_wraps_legacy_payload() -> None:
  payload = {'routers': {'api': {'rule': 'Host(`api.example.com`)'}}, 'services': {}}

  assert normalize_route(payload) == {'http': payload}


def test_normalize_route_unwraps_double_nested_http() -> None:
  inner = {'routers': {'api': {'rule': 'Host(`api.example.com`)'}}, 'services': {}}

  assert normalize_route({'http': {'http': inner}}) == {'http': inner}
  assert normalize_route({'http': inner}) == {'http': inner}


# --- RouteRegistry ------------------------------------------------------------


def test_registry_register_adds_and_serves_a_route() -> None:
  registry = RouteRegistry()

  registry.register(FULL_ROUTE_ID, make_route())

  assert len(registry) == 1
  state = registry.state()
  assert state['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'
  assert FULL_ROUTE_ID in state['_seto_registry']['routes']


def test_registry_ttl_clock_starts_at_first_registration(mocker) -> None:
  clock = mocker.patch('seto.core.provider_server.time.monotonic', return_value=100.0)
  registry = RouteRegistry(ttl_seconds=5, probe=lambda _route: True)

  registry.register(FULL_ROUTE_ID, make_route())
  clock.return_value = 103.0
  registry.register(FULL_ROUTE_ID, make_route())
  registry.register(FULL_ROUTE_ID, make_route())

  # Renewals are free: the route expires 5s after the FIRST registration, even
  # though it was renewed twice in the meantime.
  assert not registry.drop_expired(now=104.0)
  assert len(registry) == 1

  dropped = registry.drop_expired(now=106.0)

  assert dropped == [FULL_ROUTE_ID]
  assert len(registry) == 0


def test_registry_drops_route_whose_backend_stopped_answering() -> None:
  alive = {'live': True}
  registry = RouteRegistry(ttl_seconds=60, probe=lambda _route: alive['live'])

  registry.register(FULL_ROUTE_ID, make_route())
  assert not registry.drop_expired(now=time.monotonic() + 1.0)

  alive['live'] = False

  assert registry.drop_expired(now=time.monotonic() + 1.0) == [FULL_ROUTE_ID]
  assert len(registry) == 0


def test_registry_keeps_route_whose_probe_is_disabled() -> None:
  registry = RouteRegistry(ttl_seconds=60, probe=lambda _route: False)

  registry.register(FULL_ROUTE_ID, {**make_route(), 'probe': False})

  assert not registry.drop_expired(now=time.monotonic() + 1.0)
  assert len(registry) == 1


def test_registry_remove() -> None:
  registry = RouteRegistry()
  registry.register(FULL_ROUTE_ID, make_route())

  assert registry.remove(FULL_ROUTE_ID) is True
  assert registry.remove(FULL_ROUTE_ID) is False
  assert len(registry) == 0
  assert registry.http_state() == {'http': {'routers': {}, 'services': {}}}


def test_registry_state_sections() -> None:
  registry = RouteRegistry()
  registry.register(FULL_ROUTE_ID, make_route())

  assert list(registry.http_state()) == ['http']
  assert list(registry.registry_state()) == ['_seto_registry']
  assert list(registry.state()) == ['http', '_seto_registry']


def test_group_routes_round_trips_registry_state() -> None:
  registry = RouteRegistry()
  registry.register(FULL_ROUTE_ID, make_route())

  grouped = group_routes(registry.state())

  assert list(grouped) == [FULL_ROUTE_ID]
  assert grouped[FULL_ROUTE_ID]['http'] == make_route()['http']


@pytest.mark.parametrize(
  'payload',
  [
    {},
    {'http': {}},
    {'_seto_registry': {}},
    {'_seto_registry': {'routes': []}},
    {'http': {'routers': {'zigbee2mqtt': {'rule': 'Host(`z.local`)'}}, 'services': {}}},
  ],
)
def test_group_routes_returns_the_bare_http_payload_as_one_route(payload) -> None:
  grouped = group_routes(payload)

  # A bare /traefik payload carries no registry: the provider keeps it as the
  # single legacy route so its Traefik configuration is still served.
  assert list(grouped) == ['']
  assert grouped['']['http'] == payload.get('http', {})


def test_start_reaper_sweeps_expired_routes(mocker) -> None:
  clock = mocker.patch('seto.core.provider_server.time.monotonic', return_value=100.0)
  registry = RouteRegistry(ttl_seconds=1, probe=lambda _route: True)
  registry.register(FULL_ROUTE_ID, make_route())
  clock.return_value = 200.0

  thread = start_reaper(registry, 0.05)

  assert isinstance(thread, threading.Thread)
  assert thread.daemon is True

  deadline = time.monotonic() + 2.0

  while len(registry) and time.monotonic() < deadline:
    time.sleep(0.01)

  assert len(registry) == 0


# --- HTTP server --------------------------------------------------------------


@pytest.fixture
def provider_server(provider_recorder, mocker):
  # The autouse recorder answers every Traefik provider call; loopback traffic
  # to this real server must reach it instead of being answered in memory.
  server = build_server(ttl_seconds=7, address=('127.0.0.1', 0))
  base_url = f'http://127.0.0.1:{server.server_address[1]}'
  real_urlopen = provider_recorder._real_urlopen

  def _urlopen(request, timeout=5):
    url = request if isinstance(request, str) else request.full_url

    if url.startswith(base_url):
      return real_urlopen(request, timeout=timeout)

    return provider_recorder.urlopen(request, timeout)

  mocker.patch('seto.core.provider_server.urllib_request.urlopen', side_effect=_urlopen)

  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()

  try:
    yield server
  finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)


def request_url(server, path: str) -> str:
  host, port = server.server_address[:2]

  return f'http://{host}:{port}{path}'


def call(server, path: str, *, method: str = 'GET', body: dict | None = None):
  """Send a request and return ``(status, decoded JSON or None)``."""
  data = json.dumps(body).encode('utf-8') if body is not None else None
  request = urllib_request.Request(
    request_url(server, path),
    data=data,
    headers={'Content-Type': 'application/json'},
    method=method,
  )
  print('CALL', method, request.full_url, data is not None)

  try:
    with urllib_request.urlopen(request, timeout=5) as response:
      raw = response.read()
      status = response.getcode()
  except urllib_error.HTTPError as error:
    return error.code, None

  return status, json.loads(raw.decode('utf-8')) if raw else None


def send_raising(request) -> int:
  """Send ``request`` and return the HTTP error code it was rejected with."""
  try:
    with urllib_request.urlopen(request, timeout=5) as response:
      return response.getcode()
  except urllib_error.HTTPError as error:
    return error.code


def test_server_registry_reachable_from_handler() -> None:
  with build_server(ttl_seconds=7, address=('127.0.0.1', 0)) as server:
    assert isinstance(server.RequestHandlerClass.registry, RouteRegistry)
    assert server.RequestHandlerClass.registry.ttl_seconds == 7


def test_put_route_is_served_on_get(provider_server) -> None:
  status, _payload = call(
    provider_server, f'/traefik/{FULL_ROUTE_ID}', method='PUT', body=make_route()
  )

  assert status == 204

  _status, state = call(provider_server, '/traefik')

  assert state['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'
  assert FULL_ROUTE_ID in state['_seto_registry']['routes']


def test_get_health_reports_ttl_and_route_count(provider_server) -> None:
  status, _payload = call(
    provider_server, f'/traefik/{FULL_ROUTE_ID}', method='PUT', body=make_route()
  )

  assert status == 204

  status, health = call(provider_server, '/health')

  assert status == 200
  assert health == {'ttl': 7, 'routes': 1}


def test_delete_route_removes_it(provider_server) -> None:
  status, _payload = call(
    provider_server, f'/traefik/{FULL_ROUTE_ID}', method='PUT', body=make_route()
  )

  assert status == 204

  status, _payload = call(provider_server, f'/traefik/{FULL_ROUTE_ID}', method='DELETE')

  assert status == 204

  _status, state = call(provider_server, '/traefik')

  assert state['http']['routers'] == {}
  assert state['_seto_registry']['routes'] == {}


def test_invalid_json_body_is_rejected(provider_server) -> None:
  request = urllib_request.Request(
    request_url(provider_server, f'/traefik/{FULL_ROUTE_ID}'),
    data=b'{not json',
    headers={'Content-Type': 'application/json'},
    method='PUT',
  )

  status = send_raising(request)

  assert status == 400


def test_unknown_path_is_not_found(provider_server) -> None:
  status, _payload = call(provider_server, '/nope')

  assert status == 404
