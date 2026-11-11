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
# pylint: disable=import-error,protected-access,invalid-name,no-member
import json
from urllib import error as urllib_error

import pytest
from seto.core.traefik import build_traefik_http_provider_state
from seto.core.traefik import build_traefik_route_records
from seto.core.traefik import DEFAULT_TRAEFIK_HTTP_ENDPOINT
from seto.core.traefik import DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS
from seto.core.traefik import expire_stale_traefik_routes
from seto.core.traefik import group_provider_routes
from seto.core.traefik import register_traefik_http_route
from seto.core.traefik import resolve_traefik_route_ttl
from seto.core.traefik import ROUTE_CONTROL_KEYS
from seto.core.traefik import route_provider_record
from seto.core.traefik import route_server_urls
from seto.core.traefik import traefik_route_is_alive
from seto.core.traefik import TRAEFIK_ROUTE_TTL_ENV
from seto.core.traefik import traefik_route_ttl_seconds

FULL_ROUTE_ID = 'oremi_iot_mali-zigbee2mqtt'


def make_route(service_name: str = 'zigbee2mqtt', *, hostname: str = '10.42.0.5') -> dict:
  """Build a dynamic route shaped like ``extract_traefik_route`` output."""
  return {
    'http': {
      'routers': {
        service_name: {
          'rule': f'Host(`{service_name}.example.com`)',
          'service': service_name,
          'entryPoints': ['web'],
        }
      },
      'services': {
        service_name: {
          'loadBalancer': {'servers': [{'url': f'http://{hostname}:32771'}]},
        }
      },
    },
    'last_seen': 1700000000,
    'route_id': service_name,
  }


class RecordingResponse:
  """Minimal stand-in for the urllib response object."""

  def __init__(self, status: int = 204) -> None:
    self._status = status

  def __enter__(self) -> 'RecordingResponse':
    return self

  def __exit__(self, exc_type, exc, traceback) -> bool:
    return False

  def getcode(self) -> int:
    return self._status


class RecordingUrlOpen:
  """``urlopen`` stub recording every request and delegating the outcome."""

  def __init__(self, responder) -> None:
    self._responder = responder
    self.requests: list = []

  def __call__(self, request, timeout=5):
    self.requests.append(request)
    return self._responder(request, timeout)

  @property
  def payloads(self) -> list[dict]:
    return [json.loads(request.data.decode('utf-8')) for request in self.requests]


def alive_probe(_route: dict) -> bool:
  return True


def dead_probe(_route: dict) -> bool:
  return False


# --- TTL resolution -----------------------------------------------------------


def test_default_route_ttl_constant_is_five_seconds() -> None:
  assert DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS == 5


def test_route_control_keys_are_the_internal_bookkeeping_keys() -> None:
  assert ROUTE_CONTROL_KEYS == ('last_seen', 'route_id', 'probe')


def test_route_ttl_env_name() -> None:
  assert TRAEFIK_ROUTE_TTL_ENV == 'SETO_TRAEFIK_ROUTE_TTL'


def test_route_ttl_seconds_falls_back_to_default_without_env(mocker) -> None:
  mocker.patch.dict('os.environ', {}, clear=True)

  assert traefik_route_ttl_seconds() == DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS


def test_route_ttl_seconds_reads_environment_override(mocker) -> None:
  mocker.patch.dict('os.environ', {TRAEFIK_ROUTE_TTL_ENV: '9'}, clear=True)

  assert traefik_route_ttl_seconds() == 9


def test_route_ttl_seconds_tolerates_surrounding_whitespace(mocker) -> None:
  mocker.patch.dict('os.environ', {TRAEFIK_ROUTE_TTL_ENV: ' 12 '}, clear=True)

  assert traefik_route_ttl_seconds() == 12


@pytest.mark.parametrize('raw_value', ['', '   ', 'abc', '0', '-3', '1.5'])
def test_route_ttl_seconds_rejects_invalid_environment_values(mocker, raw_value: str) -> None:
  mocker.patch.dict('os.environ', {TRAEFIK_ROUTE_TTL_ENV: raw_value}, clear=True)

  assert traefik_route_ttl_seconds() == DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS


@pytest.mark.parametrize(
  'value',
  [None, '', '   ', 'abc', '0', '-3', 0, -1, 1.5, True, False],
)
def test_resolve_route_ttl_falls_back_for_invalid_values(value) -> None:
  assert resolve_traefik_route_ttl(value) == DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS


@pytest.mark.parametrize(('value', 'expected'), [('9', 9), (' 9 ', 9), (9, 9), (120, 120)])
def test_resolve_route_ttl_accepts_positive_values(value, expected: int) -> None:
  assert resolve_traefik_route_ttl(value) == expected


def test_resolve_route_ttl_prefers_explicit_value_over_environment(mocker) -> None:
  mocker.patch.dict('os.environ', {TRAEFIK_ROUTE_TTL_ENV: '9'}, clear=True)

  assert resolve_traefik_route_ttl('3') == 3


# --- provider records and payloads -------------------------------------------


def test_route_provider_record_strips_control_keys_and_marks_probe() -> None:
  route = {**make_route(), 'last_seen': 1700000000, 'route_id': 'zigbee2mqtt', 'probe': False}

  record = route_provider_record(FULL_ROUTE_ID, route)

  assert 'last_seen' not in record
  assert record['route_id'] == 'zigbee2mqtt'
  assert record['probe'] is True
  assert record['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'


def test_route_provider_record_omits_probe_when_not_requested() -> None:
  record = route_provider_record(FULL_ROUTE_ID, make_route(), probe_route=False)

  assert 'probe' not in record
  assert record['route_id'] == 'zigbee2mqtt'


def test_route_provider_record_falls_back_to_route_id_argument() -> None:
  route = make_route()
  del route['route_id']

  record = route_provider_record(FULL_ROUTE_ID, route)

  assert record['route_id'] == FULL_ROUTE_ID


def test_provider_state_does_not_leak_route_control_keys() -> None:
  record = route_provider_record(FULL_ROUTE_ID, make_route())

  payload = build_traefik_http_provider_state({FULL_ROUTE_ID: record})

  assert 'last_seen' not in payload
  assert 'route_id' not in payload
  assert 'probe' not in payload
  assert '_seto_registry' not in payload
  assert payload['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'
  assert (
    payload['http']['services']['zigbee2mqtt']['loadBalancer']['servers'][0]['url']
    == 'http://10.42.0.5:32771'
  )


def test_build_route_records_wraps_records_in_registry() -> None:
  records = build_traefik_route_records({FULL_ROUTE_ID: make_route()})

  assert list(records) == ['_seto_registry']
  assert list(records['_seto_registry']['routes']) == [FULL_ROUTE_ID]

  record = records['_seto_registry']['routes'][FULL_ROUTE_ID]
  assert record['route_id'] == 'zigbee2mqtt'
  assert record['probe'] is True
  assert 'last_seen' not in record


def test_build_route_records_can_disable_probing() -> None:
  records = build_traefik_route_records(
    {FULL_ROUTE_ID: make_route()},
    probe_route=False,
  )

  assert 'probe' not in records['_seto_registry']['routes'][FULL_ROUTE_ID]


# --- register one route -------------------------------------------------------


def test_register_route_puts_single_route_on_endpoint(mocker, provider_recorder) -> None:
  published, record = register_traefik_http_route(
    FULL_ROUTE_ID,
    make_route(),
    endpoint='http://seto-provider:8080/traefik',
  )

  assert published is True
  assert record['route_id'] == 'zigbee2mqtt'
  assert record['probe'] is True

  # Published under the full registry id, so the provider attributes the route
  # to this stack and can expire it on its own.
  assert provider_recorder.requests == [('PUT', FULL_ROUTE_ID)]

  served = provider_recorder.http['routers']['zigbee2mqtt']
  assert served['rule'] == 'Host(`zigbee2mqtt.example.com`)'
  assert 'last_seen' not in served
  assert 'route_id' not in served
  assert 'probe' not in served


def test_register_route_returns_cleaned_record_without_probe_marker(mocker) -> None:
  published, record = register_traefik_http_route(
    FULL_ROUTE_ID,
    {**make_route(), 'probe': True, 'last_seen': 1700000000},
    probe_route=False,
    endpoint='http://seto-provider:8080/traefik',
  )

  assert published is True
  assert 'probe' not in record
  assert 'last_seen' not in record
  assert record['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'


def test_register_route_falls_back_to_localhost_endpoint(mocker) -> None:
  attempts: list[str] = []

  def _responder(request, _timeout):
    attempts.append(request.full_url)

    if request.full_url.startswith('http://seto-provider:8080/traefik'):
      raise urllib_error.URLError('dns failure')

    return RecordingResponse()

  mocker.patch(
    'seto.core.traefik.urllib_request.urlopen',
    side_effect=RecordingUrlOpen(_responder),
  )

  published, _record = register_traefik_http_route(
    FULL_ROUTE_ID,
    make_route(),
    endpoint='http://seto-provider:8080/traefik',
  )

  assert published is True
  assert attempts == [
    f'http://seto-provider:8080/traefik/{FULL_ROUTE_ID}',
    f'http://localhost:8080/traefik/{FULL_ROUTE_ID}',
  ]


def test_register_route_reports_failure_when_no_endpoint_answers(mocker, provider_recorder) -> None:
  provider_recorder.fail = True

  published, record = register_traefik_http_route(
    FULL_ROUTE_ID,
    make_route(),
    endpoint='http://seto-provider:8080/traefik',
  )

  assert published is False
  # The cleaned record is still returned so callers can build a fallback.
  assert record['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'


def test_register_route_uses_default_endpoint_when_omitted(mocker, provider_recorder) -> None:
  mocker.patch.dict('os.environ', {}, clear=True)

  published, _record = register_traefik_http_route('oremi_iot_mali-api', make_route('api'))

  assert published is True
  assert provider_recorder.requests == [('PUT', 'oremi_iot_mali-api')]
  assert 'api' in provider_recorder.http['routers']
  assert DEFAULT_TRAEFIK_HTTP_ENDPOINT.endswith('/traefik')


# --- grouping provider state --------------------------------------------------


def test_group_provider_routes_inverts_build_route_records() -> None:
  routes = {
    'oremi_iot_mali-zigbee2mqtt': make_route('zigbee2mqtt'),
    'oremi_iot_mali-api': make_route('api'),
  }
  state = {
    **build_traefik_http_provider_state(
      {route_id: route_provider_record(route_id, route) for route_id, route in routes.items()}
    ),
    **build_traefik_route_records(routes),
  }

  grouped = group_provider_routes(state)

  assert sorted(grouped) == sorted(routes)

  for route_id, route in routes.items():
    assert grouped[route_id]['route_id'] == route['route_id']
    assert grouped[route_id]['probe'] is True
    assert grouped[route_id]['http'] == route['http']


def test_group_provider_routes_returns_empty_for_http_only_payload() -> None:
  record = route_provider_record(FULL_ROUTE_ID, make_route())
  payload = build_traefik_http_provider_state({FULL_ROUTE_ID: record})

  assert not group_provider_routes(payload)


def test_group_provider_routes_round_trips_into_expire() -> None:
  routes = {FULL_ROUTE_ID: make_route()}
  state = {
    **build_traefik_http_provider_state(
      {FULL_ROUTE_ID: route_provider_record(FULL_ROUTE_ID, make_route())}
    ),
    **build_traefik_route_records(routes),
  }

  grouped = group_provider_routes(state)
  surviving = expire_stale_traefik_routes(
    grouped,
    registered_at={FULL_ROUTE_ID: 100.0},
    now=101.0,
    ttl_seconds=5,
    probe=alive_probe,
  )

  assert list(surviving) == [FULL_ROUTE_ID]


@pytest.mark.parametrize(
  'invalid_state',
  [
    None,
    {},
    {'http': {'routers': {}, 'services': {}}},
    {'_seto_registry': {}},
    {'_seto_registry': {'routes': []}},
    {'_seto_registry': {'routes': 'nope'}},
    {'_seto_registry': 'nope'},
  ],
)
def test_group_provider_routes_returns_empty_for_invalid_payloads(invalid_state) -> None:
  assert not group_provider_routes(invalid_state)


def test_group_provider_routes_skips_non_dict_records() -> None:
  state = {
    '_seto_registry': {
      'routes': {
        FULL_ROUTE_ID: route_provider_record(FULL_ROUTE_ID, make_route()),
        'broken': 'nope',
      }
    }
  }

  grouped = group_provider_routes(state)

  assert list(grouped) == [FULL_ROUTE_ID]


# --- liveness probe -----------------------------------------------------------


def test_route_server_urls_reads_load_balancer_servers() -> None:
  assert route_server_urls(make_route()) == ['http://10.42.0.5:32771']


@pytest.mark.parametrize(
  'route',
  [
    {},
    {'http': {}},
    {'http': {'services': {}}},
    {'http': {'services': {'api': {'loadBalancer': {'servers': []}}}}},
  ],
)
def test_route_server_urls_returns_empty_without_backends(route: dict) -> None:
  assert not route_server_urls(route)


def test_route_is_alive_returns_false_without_server_urls() -> None:
  assert traefik_route_is_alive({'http': {'routers': {}, 'services': {}}}) is False
  assert traefik_route_is_alive({}) is False


def test_route_is_alive_probes_backend_urls(mocker) -> None:
  probed: list[tuple[str, str]] = []

  def _responder(request, _timeout):
    probed.append((request.get_method(), request.full_url))
    return RecordingResponse(200)

  mocker.patch(
    'seto.core.traefik.urllib_request.urlopen',
    side_effect=RecordingUrlOpen(_responder),
  )

  assert traefik_route_is_alive(make_route()) is True
  assert probed == [('HEAD', 'http://10.42.0.5:32771')]


def test_route_is_alive_accepts_any_http_status(mocker) -> None:
  def _responder(_request, _timeout):
    raise urllib_error.HTTPError(
      url='http://10.42.0.5:32771',
      code=503,
      msg='Service Unavailable',
      hdrs=None,
      fp=None,
    )

  mocker.patch(
    'seto.core.traefik.urllib_request.urlopen',
    side_effect=RecordingUrlOpen(_responder),
  )

  assert traefik_route_is_alive(make_route()) is True


def test_route_is_alive_returns_false_when_backend_is_unreachable(
  mocker, provider_recorder
) -> None:
  provider_recorder.fail = True

  assert traefik_route_is_alive(make_route()) is False


def test_route_is_alive_stops_at_first_answering_backend(mocker) -> None:
  route = make_route()
  route['http']['services']['zigbee2mqtt']['loadBalancer']['servers'] = [
    {'url': 'http://10.42.0.5:32771'},
    {'url': 'http://10.42.0.6:32771'},
  ]
  urlopen = RecordingUrlOpen(lambda _request, _timeout: RecordingResponse(200))
  mocker.patch('seto.core.traefik.urllib_request.urlopen', side_effect=urlopen)

  assert traefik_route_is_alive(route) is True
  assert len(urlopen.requests) == 1


# --- expiry -------------------------------------------------------------------


def test_expire_drops_route_older_than_ttl() -> None:
  routes = {'stack-old': make_route('old'), 'stack-fresh': make_route('fresh')}
  registered_at = {'stack-old': 100.0, 'stack-fresh': 110.0}

  surviving = expire_stale_traefik_routes(
    routes,
    registered_at=registered_at,
    now=112.0,
    ttl_seconds=5,
    probe=alive_probe,
  )

  assert list(surviving) == ['stack-fresh']


def test_expire_keeps_route_within_ttl() -> None:
  routes = {'stack-fresh': make_route('fresh')}

  surviving = expire_stale_traefik_routes(
    routes,
    registered_at={'stack-fresh': 100.0},
    now=103.0,
    ttl_seconds=5,
    probe=alive_probe,
  )

  assert list(surviving) == ['stack-fresh']


def test_expire_keeps_route_exactly_at_ttl_boundary() -> None:
  routes = {'stack-route': make_route('route')}

  surviving = expire_stale_traefik_routes(
    routes,
    registered_at={'stack-route': 100.0},
    now=105.0,
    ttl_seconds=5,
    probe=alive_probe,
  )

  assert list(surviving) == ['stack-route']


def test_expire_drops_route_whose_backend_stopped_answering() -> None:
  routes = {'stack-dead': make_route('dead'), 'stack-live': make_route('live')}

  surviving = expire_stale_traefik_routes(
    routes,
    registered_at={'stack-dead': 100.0, 'stack-live': 100.0},
    now=101.0,
    ttl_seconds=5,
    probe=lambda route: 'live' in json.dumps(route),
  )

  assert list(surviving) == ['stack-live']


def test_expire_defaults_probe_to_liveness_check(mocker) -> None:
  probed: list[str] = []

  def _responder(request, _timeout):
    probed.append(request.full_url)
    raise urllib_error.URLError('unreachable')

  mocker.patch(
    'seto.core.traefik.urllib_request.urlopen',
    side_effect=RecordingUrlOpen(_responder),
  )

  surviving = expire_stale_traefik_routes(
    {FULL_ROUTE_ID: make_route()},
    registered_at={FULL_ROUTE_ID: 100.0},
    now=101.0,
    ttl_seconds=5,
  )

  # Without an explicit probe the route is dropped once its backend stops answering.
  assert not surviving
  assert probed == ['http://10.42.0.5:32771']


def test_expire_uses_default_ttl_seconds(mocker) -> None:
  mocker.patch.dict('os.environ', {}, clear=True)
  routes = {'stack-old': make_route('old')}

  surviving = expire_stale_traefik_routes(
    routes,
    registered_at={'stack-old': 100.0},
    now=100.0 + DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS + 1,
    probe=alive_probe,
  )

  assert not surviving


def test_expire_honours_environment_ttl_override(mocker) -> None:
  mocker.patch.dict('os.environ', {TRAEFIK_ROUTE_TTL_ENV: '2'}, clear=True)
  routes = {'stack-route': make_route('route')}

  surviving = expire_stale_traefik_routes(
    routes,
    registered_at={'stack-route': 100.0},
    now=104.0,
    probe=alive_probe,
  )

  assert not surviving


def test_expire_keeps_routes_without_registration_timestamp() -> None:
  routes = {'stack-route': make_route('route')}

  surviving = expire_stale_traefik_routes(
    routes,
    registered_at={},
    now=1000.0,
    ttl_seconds=5,
    probe=alive_probe,
  )

  assert list(surviving) == ['stack-route']


def test_expire_does_not_mutate_input_routes() -> None:
  routes = {'stack-route': make_route('route')}
  snapshot = json.dumps(routes, sort_keys=True)

  expire_stale_traefik_routes(
    routes,
    registered_at={'stack-route': 100.0},
    now=100.0,
    ttl_seconds=5,
    probe=dead_probe,
  )

  assert json.dumps(routes, sort_keys=True) == snapshot


def test_expire_returns_empty_when_nothing_survives() -> None:
  assert not expire_stale_traefik_routes(
    {}, registered_at={}, now=0.0, ttl_seconds=5, probe=alive_probe
  )
