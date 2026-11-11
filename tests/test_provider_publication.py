# Copyright 2024-2026 Sébastien Demanou. All Rights Reserved.
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
"""Compose publication of Traefik HTTP provider routes."""
# pylint: disable=import-error,protected-access
from unittest.mock import MagicMock

from seto.core.docker import DockerCompose
from seto.core.driver import Driver
from seto.core.shell import Shell


def swarm_node(hostname: str, *, addr: str = '10.42.0.5') -> MagicMock:
  node = MagicMock()
  node.attrs = {
    'Description': {'Hostname': hostname},
    'Status': {'Addr': addr},
    'Spec': {'Role': 'worker', 'Labels': {}},
  }

  return node


def docker_driver(mocker, contexts, *, stack_id: str = 'oremi_iot_mali') -> MagicMock:
  mocker.patch('seto.core.shell.resolve_hostname', return_value='127.0.0.1')
  mocker.patch('socket.gethostname', return_value='local-host')

  driver = MagicMock(spec=Driver)
  driver.stack_id = stack_id
  driver.debug = False
  driver.storage_node = False
  driver.host = MagicMock(spec=Shell)
  driver.contexts = contexts

  return driver


def traefik_compose_config() -> dict:
  return {
    'x-placement': ['node.hostname==mali'],
    'services': {
      'zigbee2mqtt': {
        'image': 'koenkk/zigbee2mqtt:2',
        'ports': ['58080:8080'],
        'labels': {
          'traefik.http.routers.zigbee2mqtt.rule': 'Host(`zigbee2mqtt.example.com`)',
          'traefik.http.services.zigbee2mqtt.loadbalancer.server.port': '8080',
        },
      },
      'api': {
        'image': 'busybox',
        'labels': {
          'traefik.http.routers.api.rule': 'Host(`api.example.com`)',
          'traefik.http.services.api.loadbalancer.server.port': '9000',
        },
      },
    },
  }


def traefik_compose(mocker, contexts_factory, config=None) -> DockerCompose:
  contexts = contexts_factory(nodes={'mali': 'seto-mali'})
  driver = docker_driver(mocker, contexts, stack_id='oremi_iot_mali')

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('mali')]

  compose = DockerCompose(
    config=config if config is not None else traefik_compose_config(),
    driver=driver,
    client=client,
  )
  compose._traefik_service_specs = compose._extract_traefik_service_specs()

  return compose


def published_ports_stub(command, _nodename):
  if command.endswith('port zigbee2mqtt 8080'):
    return '0.0.0.0:49152\n'
  if command.endswith('port api 9000'):
    return '0.0.0.0:49153\n'
  return ''


def test_compose_publish_registers_each_route_individually(mocker, contexts_factory):
  compose = traefik_compose(mocker, contexts_factory)

  mocker.patch.object(compose, '_exec_output', side_effect=published_ports_stub)

  registered = []

  def _register_stub(route_id, route, *, probe_route=True, endpoint=None):
    registered.append((route_id, route, probe_route, endpoint))
    return True, {key: value for key, value in route.items() if key != 'probe'}

  register_mock = mocker.patch(
    'seto.core.docker.register_traefik_http_route',
    side_effect=_register_stub,
  )
  watchdog_mock = mocker.patch(
    'seto.core.docker.DockerCompose._start_traefik_route_watchdog',
    create=True,
  )

  compose._publish_traefik_routes('mali')

  assert [entry[0] for entry in registered] == [
    'oremi_iot_mali-zigbee2mqtt',
    'oremi_iot_mali-api',
  ]
  assert register_mock.call_count == 2

  _route_id, route, probe_route, _endpoint = registered[0]
  assert probe_route is True
  assert route['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'
  assert (
    route['http']['services']['zigbee2mqtt']['loadBalancer']['servers'][0]['url']
    == 'http://10.42.0.5:49152'
  )

  # Both routes were published directly from the host: no watchdog needed.
  watchdog_mock.assert_not_called()


def test_compose_publish_installs_watchdog_when_direct_publish_fails(mocker, contexts_factory):
  compose = traefik_compose(mocker, contexts_factory)

  mocker.patch.object(compose, '_exec_output', side_effect=published_ports_stub)

  publish_results = {
    'oremi_iot_mali-zigbee2mqtt': False,
    'oremi_iot_mali-api': True,
  }

  def _register_stub(route_id, route, *, probe_route=True, endpoint=None):
    return publish_results[route_id], {key: value for key, value in route.items() if key != 'probe'}

  mocker.patch('seto.core.docker.register_traefik_http_route', side_effect=_register_stub)
  watchdog_mock = mocker.patch(
    'seto.core.docker.DockerCompose._start_traefik_route_watchdog',
    create=True,
  )

  compose._publish_traefik_routes('mali')

  watchdog_mock.assert_called_once()


def test_compose_publish_never_starts_the_watchdog_when_offline(mocker, contexts_factory):
  compose = traefik_compose(mocker, contexts_factory)

  mocker.patch.object(compose, '_exec_output', side_effect=published_ports_stub)
  register_mock = mocker.patch(
    'seto.core.docker.register_traefik_http_route',
    side_effect=lambda route_id, route, **kwargs: (True, {'http': route['http']}),
  )
  watchdog_mock = mocker.patch(
    'seto.core.docker.DockerCompose._start_traefik_route_watchdog',
    create=True,
  )

  compose._publish_traefik_routes('mali')

  assert register_mock.call_count == 2
  watchdog_mock.assert_not_called()


def test_compose_publish_skips_routes_with_unresolved_host_port(mocker, contexts_factory):
  compose = traefik_compose(mocker, contexts_factory)

  mocker.patch.object(compose, '_exec_output', return_value='')
  register_mock = mocker.patch(
    'seto.core.docker.register_traefik_http_route',
    side_effect=lambda route_id, route, **kwargs: (True, {'http': route['http']}),
  )

  compose._publish_traefik_routes('mali')

  register_mock.assert_not_called()


def test_compose_down_removes_routes_by_full_registry_id(mocker, contexts_factory):
  compose = traefik_compose(mocker, contexts_factory)

  removed = []
  remove_mock = mocker.patch(
    'seto.core.docker.remove_traefik_http_registry_route',
    side_effect=lambda route_id, **kwargs: removed.append(route_id) or True,
  )

  compose.down()

  assert remove_mock.call_count == 2
  assert sorted(removed) == [
    'oremi_iot_mali-api',
    'oremi_iot_mali-zigbee2mqtt',
  ]


def test_compose_down_removes_routes_without_prepared_specs(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'mali': 'seto-mali'})
  driver = docker_driver(mocker, contexts, stack_id='oremi_iot_mali')

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('mali')]

  compose = DockerCompose(config=traefik_compose_config(), driver=driver, client=client)
  mocker.patch.object(compose, '_exec')

  removed = []
  mocker.patch(
    'seto.core.docker.remove_traefik_http_registry_route',
    side_effect=lambda route_id, **kwargs: removed.append(route_id) or True,
  )

  compose.down()

  assert sorted(removed) == ['oremi_iot_mali-api', 'oremi_iot_mali-zigbee2mqtt']
