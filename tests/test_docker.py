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
from unittest.mock import MagicMock
from urllib import error as urllib_error

import pytest
from seto.core.context import DockerContextUnavailableError
from seto.core.docker import DockerCompose
from seto.core.docker import DockerSwarm
from seto.core.driver import Driver
from seto.core.shell import Shell
from seto.core.traefik import build_traefik_http_provider_state
from seto.core.traefik import extract_traefik_route
from seto.core.traefik import merge_traefik_provider_state
from seto.core.traefik import prune_stale_traefik_routes
from seto.core.traefik import remove_traefik_http_registry_route
from seto.core.traefik import sync_traefik_http_registry


def swarm_node(hostname, *, role='worker', labels=None, addr='10.42.0.5'):
  node = MagicMock()
  node.attrs = {
    'Description': {'Hostname': hostname},
    'Status': {'Addr': addr},
    'Spec': {'Role': role, 'Labels': dict(labels or {})},
  }

  return node


def docker_driver(mocker, contexts, *, debug=False, storage_node=False, stack_id='mystack'):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='127.0.0.1')
  mocker.patch('socket.gethostname', return_value='local-host')

  driver = MagicMock(spec=Driver)
  driver.stack_id = stack_id
  driver.debug = debug
  driver.storage_node = storage_node
  driver.host = MagicMock(spec=Shell)
  driver.contexts = contexts

  return driver


def test_docker_compose_targets_node_docker_context(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts, debug=True)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1')]

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

  assert compose.nodenames == ['node1']
  assert compose.matched_nodenames == ['node1']

  # info()
  compose.info()

  assert driver.host.run.call_count == 1
  command = driver.host.run.call_args.kwargs['command']
  assert command.startswith('docker --context seto-node1 compose ')
  assert 'ssh' not in command
  assert '--context default' not in command

  payload = driver.host.run.call_args.kwargs['input']
  assert 'node1' in payload
  assert '{{ .Node.Hostname }}' not in payload
  assert '{{.Node.Hostname}}' not in payload

  # deploy() targets the same context, never the current/local one
  driver.host.run.reset_mock()
  compose.deploy()

  assert driver.host.run.call_count == 1
  assert driver.host.run.call_args.kwargs['command'].startswith('docker --context seto-node1 compose ')
  assert 'node1' in driver.host.run.call_args.kwargs['input']


@pytest.mark.parametrize(
  'operation',
  ['pull', 'info', 'deploy', 'ps', 'logs', 'down'],
)
def test_docker_compose_lifecycle_operations_always_target_the_node_context(
  mocker, contexts_factory, operation
):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1')]

  compose = DockerCompose(
    config={
      'x-placement': ['node.hostname==node1'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )

  mocker.patch.object(compose, 'prepare_traefik_publication')
  mocker.patch.object(compose, '_publish_traefik_routes')
  mocker.patch.object(compose, '_remove_traefik_routes')

  getattr(compose, operation)()

  assert driver.host.run.call_count >= 1

  for call in driver.host.run.call_args_list:
    command = call.kwargs['command']
    assert command.startswith('docker --context seto-node1 compose ')
    assert '--context default' not in command
    assert not command.startswith('ssh')


def test_docker_compose_excludes_matching_node_without_docker_context(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1'), swarm_node('node2')]

  compose = DockerCompose(
    config={
      'x-placement': ['node.role==worker'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )

  # node2 matches the hardware constraints but has no Docker context: it must
  # never become a Compose deployment target.
  assert compose.matched_nodenames == ['node1', 'node2']
  assert compose.available_nodenames == ['node1']
  assert compose.nodenames == ['node1']

  compose.deploy()

  assert driver.host.run.call_count == 1
  assert 'seto-node1' in driver.host.run.call_args.kwargs['command']


def test_docker_compose_rejects_placement_without_any_docker_context(mocker, contexts_factory):
  contexts = contexts_factory()
  driver = docker_driver(mocker, contexts)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1')]

  compose = DockerCompose(
    config={'x-placement': ['node.role==worker'], 'services': {'app': {'image': 'nginx'}}},
    driver=driver,
    client=client,
  )

  assert compose.matched_nodenames == ['node1']
  assert not compose.available_nodenames

  with pytest.raises(ValueError, match='seto setup'):
    _ = compose.nodenames

  with pytest.raises(ValueError):
    compose.deploy()

  driver.host.run.assert_not_called()


def test_docker_compose_targets_every_context_backed_matching_node(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1', 'node2': 'seto-node2'})
  driver = docker_driver(mocker, contexts, debug=True)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1'), swarm_node('node2')]

  compose = DockerCompose(
    config={
      'x-placement': ['node.role==worker'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )

  assert compose.nodenames == ['node1', 'node2']

  compose.deploy()

  commands = [call.kwargs['command'] for call in driver.host.run.call_args_list]
  assert any('--context seto-node1 compose' in command for command in commands)
  assert any('--context seto-node2 compose' in command for command in commands)


def test_docker_compose_matches_host_role_and_name_constraints(mocker, contexts_factory):
  contexts = contexts_factory(
    nodes={'worker-node-1': 'seto-worker-node-1', 'manager-1': 'seto-manager-1'}
  )
  driver = docker_driver(mocker, contexts)

  client = MagicMock()
  client.nodes.list.return_value = [
    swarm_node('worker-node-1'),
    swarm_node('manager-1', role='manager'),
  ]

  by_role = DockerCompose(
    config={'x-placement': ['host.role == worker'], 'services': {'app': {'image': 'nginx'}}},
    driver=driver,
    client=client,
  )
  assert by_role.nodenames == ['worker-node-1']

  by_name = DockerCompose(
    config={
      'x-placement': ['host.name == manager-1'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )
  assert by_name.nodenames == ['manager-1']


def test_docker_compose_verifies_devices_over_administrative_ssh(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts, debug=True)

  node_shell = MagicMock(spec=Shell)
  mocker.patch.object(contexts, 'node_shell', return_value=node_shell)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1')]

  compose = DockerCompose(
    config={
      'x-placement': ['host.device.gpu == true'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )

  assert compose.nodenames == ['node1']
  node_shell.connect.assert_called_once()
  node_shell.run.assert_called_once()
  assert 'nvidia' in node_shell.run.call_args.args[0]


def test_docker_compose_excludes_node_whose_device_check_fails(mocker, contexts_factory, caplog):
  contexts = contexts_factory(nodes={'node1': 'seto-node1', 'node2': 'seto-node2'})
  driver = docker_driver(mocker, contexts)

  failing_shell = MagicMock(spec=Shell)
  failing_shell.run.side_effect = Exception('GPU device not found or not configured for Docker')
  good_shell = MagicMock(spec=Shell)

  def node_shell(hostname):
    return failing_shell if hostname == 'node1' else good_shell

  mocker.patch.object(contexts, 'node_shell', side_effect=node_shell)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1'), swarm_node('node2')]

  compose = DockerCompose(
    config={
      'x-placement': ['host.device.gpu == true'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )

  assert compose.matched_nodenames == ['node1', 'node2']
  assert compose.available_nodenames == ['node2']
  assert 'physical check failed' in caplog.text


def test_docker_compose_uses_defined_project_name_for_compose_commands(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts)

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1')]

  compose = DockerCompose(
    config={
      'name': 'original-project',
      'x-placement': ['node.hostname==node1'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )

  run_calls = []

  def _exec_stub(command, nodename, *, resolved_config=None):
    run_calls.append((command, nodename, resolved_config))

  mocker.patch.object(compose, '_exec', side_effect=_exec_stub)
  mocker.patch.object(compose, '_publish_traefik_routes')

  compose.deploy()
  compose.ps()
  compose.logs()
  compose.down()

  assert run_calls[0][0] == 'compose -p original-project -f - up -d --remove-orphans'
  assert run_calls[1][0] == 'compose -p original-project -f - ps'
  assert run_calls[2][0] == 'compose -p original-project -f - logs -n 10'
  assert run_calls[3][0] == 'compose -p original-project -f - down'


def test_docker_compose_falls_back_to_stack_id_without_project_name(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts, stack_id='fallback_stack')

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('node1')]

  compose = DockerCompose(
    config={
      'x-placement': ['node.hostname==node1'],
      'services': {'app': {'image': 'nginx'}},
    },
    driver=driver,
    client=client,
  )

  assert compose.compose_project_name == 'fallback_stack'


def test_docker_compose_build_and_push_run_on_the_local_docker_context(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts)

  compose = DockerCompose(
    config={'services': {'app': {'image': 'nginx'}}},
    driver=driver,
    client=MagicMock(),
  )

  mocker.patch.object(compose, 'prepare_traefik_publication')
  compose.localshell = MagicMock(spec=Shell)
  compose.build()
  compose.push()

  commands = [call.kwargs['command'] for call in compose.localshell.run.call_args_list]
  assert commands[0] == 'docker --context default compose -p mystack -f - build --no-cache'
  assert commands[1] == 'docker --context default compose -p mystack -f - push'


def test_docker_swarm_targets_the_manager_docker_context(mocker, contexts_factory):
  contexts = contexts_factory(storage_node=('manager1', 'seto-manager1'))
  driver = docker_driver(mocker, contexts, storage_node=MagicMock(), stack_id='demo_stack')

  swarm = DockerSwarm(config={'services': {'app': {'image': 'nginx'}}}, driver=driver)
  swarm.deploy()
  swarm.ps()
  swarm.logs()
  swarm.down()

  commands = [call.kwargs['command'] for call in driver.host.run.call_args_list]
  assert commands[0].startswith('docker stack deploy ')
  assert commands[0].endswith('demo_stack')
  assert commands[1] == 'docker --context seto-manager1 stack ps --no-trunc demo_stack'
  assert commands[2] == 'docker --context seto-manager1 stack services demo_stack'
  assert commands[3] == 'docker --context seto-manager1 service logs -n 10 demo_stack_app'
  assert commands[4] == 'docker --context seto-manager1 stack rm demo_stack'


def test_docker_swarm_falls_back_to_the_local_context_without_manager_context(
  mocker, contexts_factory
):
  contexts = contexts_factory(storage_node=('manager1', None))
  driver = docker_driver(mocker, contexts, storage_node=MagicMock())

  swarm = DockerSwarm(config={'services': {}}, driver=driver)
  swarm.down()

  assert driver.host.run.call_args.kwargs['command'] == 'docker stack rm mystack'


def test_docker_swarm_escapes_lone_dollar_signs_in_resolved_config(mocker, contexts_factory):
  contexts = contexts_factory(storage_node=('manager1', 'seto-manager1'))
  driver = docker_driver(mocker, contexts, storage_node=False)

  client = MagicMock()
  client.networks.list.return_value = []

  swarm = DockerSwarm(
    config={
      'services': {
        'dashboard': {
          'deploy': {
            'labels': {
              'traefik.http.routers.bluegate-static.rule': (
                'Host(`bluegate.umsizi.net`) && '
                'PathRegexp(`^/favicon\\.(?:ico|png|jpg|jpeg|svg|webp)$`)'
              ),
              'literal-template': '$${DOMAIN}',
            }
          }
        }
      }
    },
    driver=driver,
    client=client,
  )

  resolved = swarm.resolved_config

  assert '^/favicon\\\\.(?:ico|png|jpg|jpeg|svg|webp)$$`' in resolved
  assert '$${DOMAIN}' in resolved


def test_traefik_compose_route_registration_and_cleanup():
  service = {
    'ports': ['8080:80'],
    'labels': {
      'traefik.http.routers.api.rule': 'Host(`api.example.com`)',
      'traefik.http.routers.api.entrypoints': 'websecure',
      'traefik.http.routers.api.tls': 'true',
      'traefik.http.services.api.loadbalancer.server.port': '80',
    },
  }

  route = extract_traefik_route('api', service, hostname='node2', port=80)
  assert route is not None
  assert 'api' in route['http']['routers']
  provider = build_traefik_http_provider_state({'seto-demo-api': route})
  assert provider['http']['routers']['api']['rule'] == 'Host(`api.example.com`)'

  stale = {
    'seto-demo-api': {**route, 'last_seen': 0},
    'seto-demo-bis': {
      'http': {
        'routers': {'bis': {'rule': 'Host(`bis.example.com`)'}},
        'services': {'bis': {'loadBalancer': {'servers': [{'url': 'http://node3:8080'}]}}},
      },
      'last_seen': 0,
    },
  }

  pruned = prune_stale_traefik_routes(stale, ttl_seconds=30)
  assert not pruned


def test_traefik_provider_state_merge_keeps_registry_and_http_routes():
  current = {
    'http': {
      'routers': {'legacy': {'rule': 'Host(`legacy.example.com`)'}},
      'services': {'legacy': {'loadBalancer': {'servers': [{'url': 'http://node1:80'}]}}},
    },
    '_seto_registry': {
      'agents': {
        'demo:node1': {'stack': 'demo', 'node': 'node1', 'last_seen': 1},
      }
    },
  }

  incoming = {
    'http': {
      'routers': {'live': {'rule': 'Host(`live.example.com`)'}},
      'services': {'live': {'loadBalancer': {'servers': [{'url': 'http://node2:80'}]}}},
    },
    '_seto_registry': {
      'agents': {
        'demo:node2': {'stack': 'demo', 'node': 'node2', 'last_seen': 2},
      }
    },
  }

  merged = merge_traefik_provider_state(current, incoming)

  assert merged['http']['routers']['legacy']['rule'] == 'Host(`legacy.example.com`)'
  assert merged['http']['routers']['live']['rule'] == 'Host(`live.example.com`)'
  assert merged['_seto_registry']['agents']['demo:node1']['node'] == 'node1'
  assert merged['_seto_registry']['agents']['demo:node2']['node'] == 'node2'


def test_extract_traefik_route_ignores_non_dict_deploy_metadata():
  service = {
    'deploy': [],
    'labels': {
      'traefik.http.routers.api.rule': 'Host(`api.example.com`)',
      'traefik.http.services.api.loadbalancer.server.port': '80',
    },
  }

  route = extract_traefik_route('api', service, hostname='node2', port=80)

  assert route is not None
  assert route['http']['routers']['api']['rule'] == 'Host(`api.example.com`)'


def test_extract_traefik_route_supports_tls_certresolver_labels():
  service = {
    'labels': {
      'traefik.http.routers.zigbee2mqtt.service': 'zigbee2mqtt',
      'traefik.http.routers.zigbee2mqtt.rule': 'Host(`zigbee2mqtt.example.com`)',
      'traefik.http.routers.zigbee2mqtt.tls': 'true',
      'traefik.http.routers.zigbee2mqtt.tls.certresolver': 'letsencrypt',
      'traefik.http.routers.zigbee2mqtt.entryPoints': 'websecure',
      'traefik.http.routers.zigbee2mqtt.middlewares': 'secured@swarm',
      'traefik.http.services.zigbee2mqtt.loadbalancer.server.port': '8080',
    },
  }

  route = extract_traefik_route(
    'zigbee2mqtt',
    service,
    hostname='192.168.1.146',
    port=32771,
    external_port=32771,
  )

  assert route is not None
  router = route['http']['routers']['zigbee2mqtt']
  assert router['entryPoints'] == ['websecure']
  assert router['middlewares'] == ['secured@swarm']
  assert router['tls']['certResolver'] == 'letsencrypt'
  assert (
    route['http']['services']['zigbee2mqtt']['loadBalancer']['servers'][0]['url']
    == 'http://192.168.1.146:32771'
  )


def test_sync_traefik_http_registry_falls_back_to_localhost(mocker):
  class _Response:
    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc, tb):
      return False

    @staticmethod
    def getcode():
      return 204

  def _urlopen(request, timeout=5):
    if request.full_url.startswith('http://seto-provider:8080/traefik'):
      raise urllib_error.URLError('dns failure')
    if request.full_url.startswith('http://localhost:8080/traefik'):
      return _Response()
    raise AssertionError(f'unexpected url: {request.full_url}')

  mocker.patch('seto.core.traefik.urllib_request.urlopen', side_effect=_urlopen)

  ok = sync_traefik_http_registry(
    {
      'seto-demo-api': {
        'http': {
          'routers': {'api': {'rule': 'Host(`api.example.com`)'}},
          'services': {
            'api': {'loadBalancer': {'servers': [{'url': 'http://node1:8080'}]}},
          },
        },
        'last_seen': 1,
      }
    },
    endpoint='http://seto-provider:8080/traefik',
  )

  assert ok is True


def test_remove_traefik_http_registry_route_falls_back_to_localhost(mocker):
  class _Response:
    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc, tb):
      return False

    @staticmethod
    def getcode():
      return 204

  def _urlopen(request, timeout=5):
    if request.full_url.startswith('http://seto-provider:8080/traefik/api'):
      raise urllib_error.URLError('dns failure')
    if request.full_url.startswith('http://localhost:8080/traefik/api'):
      return _Response()
    raise AssertionError(f'unexpected url: {request.full_url}')

  mocker.patch('seto.core.traefik.urllib_request.urlopen', side_effect=_urlopen)

  ok = remove_traefik_http_registry_route('api', endpoint='http://seto-provider:8080/traefik')

  assert ok is True


def test_compose_publishes_only_traefik_services_with_router_and_port(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'mali': 'seto-mali'})
  driver = docker_driver(mocker, contexts, stack_id='oremi_iot_mali')

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('mali')]

  config = {
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
      'ignored': {
        'image': 'busybox',
        'labels': {
          'traefik.http.routers.ignored.rule': 'Host(`ignored.example.com`)',
        },
      },
    },
  }

  compose = DockerCompose(config=config, driver=driver, client=client)
  specs = compose._extract_traefik_service_specs()
  compose._traefik_service_specs = specs

  assert specs['zigbee2mqtt']['target_ports'] == {'zigbee2mqtt': 8080}
  assert compose.config['services']['zigbee2mqtt']['ports'] == ['8080']
  assert 'traefik.http.routers.zigbee2mqtt.rule' not in compose.config['services'][
    'zigbee2mqtt'
  ].get('labels', {})
  assert 'traefik.http.routers.ignored.rule' not in compose.config['services']['ignored'].get(
    'labels', {}
  )

  mocker.patch.object(compose, '_exec_output', return_value='0.0.0.0:49152\n')
  attempted_endpoints = []

  def _sync_stub(_routes, *, endpoint=None):
    attempted_endpoints.append(endpoint)
    return endpoint == 'http://10.42.0.5:8080/traefik'

  sync_mock = mocker.patch('seto.core.docker.sync_traefik_http_registry', side_effect=_sync_stub)

  compose._publish_traefik_routes('mali')

  assert sync_mock.call_count >= 1
  assert 'http://10.42.0.5:8080/traefik' in attempted_endpoints
  routes_payload = sync_mock.call_args.args[0]
  route = routes_payload['oremi_iot_mali-zigbee2mqtt']

  assert route['http']['routers']['zigbee2mqtt']['rule'] == 'Host(`zigbee2mqtt.example.com`)'
  assert (
    route['http']['services']['zigbee2mqtt']['loadBalancer']['servers'][0]['url']
    == 'http://10.42.0.5:49152'
  )


def test_compose_publishes_distinct_host_ports_for_multiple_traefik_services(
  mocker, contexts_factory
):
  contexts = contexts_factory(nodes={'mali': 'seto-mali'})
  driver = docker_driver(mocker, contexts, stack_id='oremi_iot_mali')

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('mali')]

  config = {
    'x-placement': ['node.hostname==mali'],
    'services': {
      'ps4': {
        'image': 'busybox',
        'labels': {
          'traefik.http.routers.ps4.rule': 'Host(`ps4.example.com`)',
          'traefik.http.routers.ps4.service': 'ps4',
          'traefik.http.services.ps4.loadbalancer.server.port': '3000',
          'traefik.http.routers.ps4-ws.rule': 'Host(`ps4.example.com`) && Path(`/websockets`)',
          'traefik.http.routers.ps4-ws.service': 'ps4-ws',
          'traefik.http.services.ps4-ws.loadbalancer.server.port': '8082',
        },
      }
    },
  }

  compose = DockerCompose(config=config, driver=driver, client=client)
  specs = compose._extract_traefik_service_specs()
  compose._traefik_service_specs = specs

  assert specs['ps4']['target_ports'] == {'ps4': 3000, 'ps4-ws': 8082}
  assert compose.config['services']['ps4']['ports'] == ['3000', '8082']

  def _exec_output_stub(command, _nodename):
    if command.endswith('port ps4 3000'):
      return '0.0.0.0:32772\n'
    if command.endswith('port ps4 8082'):
      return '0.0.0.0:32773\n'
    return ''

  mocker.patch.object(compose, '_exec_output', side_effect=_exec_output_stub)

  published_payloads = []

  def _sync_stub(routes, *, endpoint=None):
    published_payloads.append((endpoint, routes))
    return True

  mocker.patch('seto.core.docker.sync_traefik_http_registry', side_effect=_sync_stub)

  compose._publish_traefik_routes('mali')

  assert published_payloads
  routes_payload = published_payloads[0][1]
  route = routes_payload['oremi_iot_mali-ps4-ws']

  assert (
    route['http']['services']['ps4']['loadBalancer']['servers'][0]['url']
    == 'http://10.42.0.5:32772'
  )
  assert (
    route['http']['services']['ps4-ws']['loadBalancer']['servers'][0]['url']
    == 'http://10.42.0.5:32773'
  )


def test_compose_publish_falls_back_to_remote_node_sync(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'mali': 'seto-mali'})
  driver = docker_driver(mocker, contexts, stack_id='oremi_iot_mali')

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('mali')]

  config = {
    'x-placement': ['node.hostname==mali'],
    'services': {
      'zigbee2mqtt': {
        'image': 'koenkk/zigbee2mqtt:2',
        'labels': {
          'traefik.http.routers.zigbee2mqtt.rule': 'Host(`zigbee2mqtt.example.com`)',
          'traefik.http.services.zigbee2mqtt.loadbalancer.server.port': '8080',
        },
      },
    },
  }

  compose = DockerCompose(config=config, driver=driver, client=client)
  specs = compose._extract_traefik_service_specs()
  compose._traefik_service_specs = specs

  def _exec_output_stub(command, _nodename):
    if command.endswith('port zigbee2mqtt 8080'):
      return '0.0.0.0:49152\n'
    return ''

  exec_output_mock = mocker.patch.object(compose, '_exec_output', side_effect=_exec_output_stub)
  node_capture_mock = mocker.patch.object(compose, '_node_capture', return_value='1\n')
  sync_mock = mocker.patch('seto.core.docker.sync_traefik_http_registry', return_value=False)

  compose._publish_traefik_routes('mali')

  assert sync_mock.call_count >= 1
  assert exec_output_mock.call_count >= 1
  node_capture_mock.assert_called_once()
  assert node_capture_mock.call_args.kwargs['hostname'] == 'mali'


def test_compose_deploy_pins_random_ports_for_manual_restart(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'mali': 'seto-mali'})
  driver = docker_driver(mocker, contexts, stack_id='oremi_iot_mali')

  client = MagicMock()
  client.nodes.list.return_value = [swarm_node('mali')]

  config = {
    'x-placement': ['node.hostname==mali'],
    'services': {
      'ps4': {
        'image': 'busybox',
        'labels': {
          'traefik.http.routers.ps4.rule': 'Host(`ps4.example.com`)',
          'traefik.http.routers.ps4.service': 'ps4',
          'traefik.http.services.ps4.loadbalancer.server.port': '3000',
          'traefik.http.routers.ps4-ws.rule': 'Host(`ps4.example.com`) && Path(`/websockets`)',
          'traefik.http.routers.ps4-ws.service': 'ps4-ws',
          'traefik.http.services.ps4-ws.loadbalancer.server.port': '8082',
        },
      }
    },
  }

  compose = DockerCompose(config=config, driver=driver, client=client)

  run_calls = []

  def _exec_stub(command, nodename, *, resolved_config=None):
    run_calls.append((command, nodename, resolved_config))

  def _exec_output_stub(command, _nodename):
    if command.endswith('port ps4 3000'):
      return '0.0.0.0:32772\n'
    if command.endswith('port ps4 8082'):
      return '0.0.0.0:32773\n'
    return ''

  mocker.patch.object(compose, '_exec', side_effect=_exec_stub)
  mocker.patch.object(compose, '_exec_output', side_effect=_exec_output_stub)
  publish_mock = mocker.patch.object(compose, '_publish_traefik_routes')

  compose.deploy()

  assert len(run_calls) == 2
  assert run_calls[0][0].endswith('up -d --remove-orphans')
  assert run_calls[1][0].endswith('up -d --remove-orphans')
  assert compose.config['services']['ps4']['ports'] == ['32772:3000', '32773:8082']
  publish_mock.assert_called_once_with('mali')


def test_docker_compose_reports_swarm_listing_failures(mocker, contexts_factory):
  contexts = contexts_factory(nodes={'node1': 'seto-node1'})
  driver = docker_driver(mocker, contexts)

  client = MagicMock()
  client.nodes.list.side_effect = Exception('ssh: connect to host node1 port 22: refused')

  compose = DockerCompose(
    config={'x-placement': ['node.role==worker'], 'services': {'app': {'image': 'nginx'}}},
    driver=driver,
    client=client,
  )

  with pytest.raises(DockerContextUnavailableError, match='refused'):
    _ = compose.matched_nodenames
