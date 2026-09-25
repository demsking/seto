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
import functools
import json
import logging
import os
import re
import shlex
import socket
from typing import Any
from urllib import parse as urllib_parse

from docker import DockerClient  # pylint: disable=import-self

from .context import DockerContextResolver
from .context import DockerContextUnavailableError
from .driver import Driver
from .parser import resolve_env_vars  # pylint: disable=deprecated-module
from .shell import Setting
from .shell import Shell
from .targets import ComposeTargetResolver
from .targets import ComposeTargets
from .traefik import DEFAULT_TRAEFIK_HTTP_ENDPOINT
from .traefik import extract_traefik_route
from .traefik import get_traefik_http_labels
from .traefik import prune_stale_traefik_routes
from .traefik import remove_traefik_http_registry_route
from .traefik import sync_traefik_http_registry

# Define the regular expression pattern to match {{ .Node.Hostname }} with optional spaces
NODE_HOSTNAME_RE = r'\{\{\s*\.Node\.Hostname\s*\}\}'

logger = logging.getLogger(__name__)


# pylint: disable=no-else-return,too-many-return-statements,invalid-name,no-else-break,consider-using-in
class Docker:
  def __init__(
    self,
    config: dict,
    driver: Driver,
    client: DockerClient | None = None,
    contexts: DockerContextResolver | None = None,
  ) -> None:
    self.driver = driver
    self.contexts = contexts if contexts is not None else driver.contexts
    self.config = config
    self._client = client
    self.localshell = Driver.get_shell(
      Setting.from_connection_string(self.hostname),
    )

  @property
  def shell(self) -> Shell:
    """Shell of the host running Ṣeto (where Docker CLI commands execute)."""
    return self.driver.host

  @property
  def client(self) -> DockerClient:
    """Docker SDK client of the Swarm manager daemon.

    The manager daemon is reached through its Docker context when ``seto setup``
    registered one; otherwise the local Docker daemon is used, which preserves
    the historical workflow of running the CLI on the Swarm manager.
    """
    if self._client is None:
      self._client = self.contexts.manager_client()

    return self._client

  @property
  def resolved_config(self) -> str:
    return _escape_docker_interpolation(resolve_env_vars(json.dumps(self.config)))

  @property
  def hostname(self) -> str:
    return socket.gethostname()

  @property
  def external_networks(self) -> list[str]:
    return [
      item.attrs['Name']
      for item in self.client.networks.list()
      if item.attrs and item.attrs['Name'].startswith(self.driver.stack_id)
    ]

  @property
  def compose_project_name(self) -> str:
    name = self.config.get('name')

    return name if isinstance(name, str) and name.strip() else self.driver.stack_id

  def compose_command(self, subcommand: str) -> str:
    return f'compose -p {shlex.quote(self.compose_project_name)} -f - {subcommand}'

  def build(self) -> None:
    # Images are always built on the host running Ṣeto: pin the local Docker
    # context so a user-switched current context cannot redirect the build.
    self.localshell.run(
      command=self.contexts.local_cli(self.compose_command('build --no-cache')),
      input=self.resolved_config,
    )

  def push(self) -> None:
    self.localshell.run(
      command=self.contexts.local_cli(self.compose_command('push')),
      input=self.resolved_config,
    )

  def pull(self) -> None:
    raise NotImplementedError()

  def info(self) -> None:
    raise NotImplementedError()

  def deploy(self) -> None:
    raise NotImplementedError()

  def ps(self) -> None:
    raise NotImplementedError()

  def logs(self) -> None:
    raise NotImplementedError()

  def down(self) -> None:
    raise NotImplementedError()


def _escape_docker_interpolation(content: str) -> str:
  """Escape lone dollar signs before piping resolved config to Docker CLIs."""
  return re.sub(r'(?<!\$)\$(?!\$)', lambda _match: '$$', content)


class DockerCompose(Docker):
  _TRAEFIK_SERVICE_PORT_RE = re.compile(
    r'^traefik\.http\.services\.([^.]+)\.loadbalancer\.server\.port$'
  )
  _TRAEFIK_PROVIDER_PORT = 8080

  def __init__(
    self,
    config: dict,
    driver: Driver,
    client: DockerClient | None = None,
    contexts: DockerContextResolver | None = None,
  ) -> None:
    super().__init__(config=config, driver=driver, client=client, contexts=contexts)
    self._traefik_service_specs: dict[str, dict[str, Any]] = {}
    self._traefik_prepared = False

  @property
  def placement(self) -> list:
    value = self.config.get('x-placement', [])

    return value if isinstance(value, list) else [value]

  def _list_swarm_nodes(self) -> list[Any]:
    if self.driver.debug:
      logger.debug('Listing Swarm nodes through the manager Docker context...')

    try:
      return list(self.client.nodes.list())
    except Exception as error:
      raise DockerContextUnavailableError(f'Unable to list the Swarm nodes: {error}') from error

  @functools.lru_cache(maxsize=128)
  def _resolve_targets(self) -> ComposeTargets:
    if self.driver.debug:
      logger.debug('Resolving Compose targets for placement: %s', self.placement)

    return ComposeTargetResolver(
      placement=self.placement,
      nodes=self._list_swarm_nodes(),
      contexts=self.contexts,
      debug=self.driver.debug,
    ).resolve()

  @property
  def matched_nodenames(self) -> list[str]:
    """Swarm node hostnames matching the placement constraints."""
    return list(self._resolve_targets().matched)

  @property
  def available_nodenames(self) -> list[str]:
    """Matched nodes that are usable Compose targets (context + device verified)."""
    return list(self._resolve_targets().deployable)

  @property
  def nodenames(self) -> list[str]:
    targets = self._resolve_targets()

    if not targets.deployable:
      raise ValueError(
        f'Unable to found a deployable node for placement "{self.placement}": matched nodes '
        f'{targets.matched} are not usable Compose targets (missing Docker context or failed '
        f"physical device verification). Run 'seto setup' to create and verify their Docker "
        f'contexts.'
      )

    return list(targets.deployable)

  @property
  def nodename(self) -> str:
    names = self.nodenames
    if not names:
      raise ValueError('No target nodes found')
    return names[0]

  def _parse_hostname_string(self, value: Any) -> list[str]:
    if not value:
      return []

    raw = str(value).strip()

    if ',' in raw:
      return [h.strip() for h in raw.split(',') if h.strip()]
    elif ' ' in raw:
      return [h.strip() for h in raw.split() if h.strip()]
    else:
      return [raw]

  def _parse_placement_string(self, value: Any) -> list[str]:
    if not value:
      return []

    raw = str(value).strip()

    if ',' in raw:
      return [p.strip() for p in raw.split(',') if p.strip()]

    return [raw]

  def _node_run(self, command: str, *, hostname: str, input: str) -> None:  # pylint: disable=redefined-builtin
    """Run a host-level command on a node over its administrative SSH connection."""
    node = self.contexts.node_shell(hostname)

    node.connect()

    try:
      node.run(command, input=input)
    finally:
      node.close()

  def _node_capture(self, command: str, *, hostname: str) -> str:
    """Capture a host-level command output from a node over SSH."""
    node = self.contexts.node_shell(hostname)

    node.connect()

    try:
      return node.run(command, quiet=True, stdout=False)
    finally:
      node.close()

  def _exec(self, command: str, nodename: str, *, resolved_config: str | None = None) -> None:
    """Run a Docker command against the Docker context of a node.

    The Docker CLI runs on the host running Ṣeto and reaches the remote Docker
    daemon through the node Docker context; no Docker command is ever executed
    over SSH or against the current Docker context.
    """
    config_to_use = resolved_config if resolved_config is not None else self.resolved_config
    self.shell.run(command=self.contexts.node_cli(nodename, command), input=config_to_use)

  def _exec_output(self, command: str, nodename: str) -> str:
    """Capture the output of a Docker command executed through a node context."""
    return self.shell.run(
      command=self.contexts.node_cli(nodename, command),
      quiet=True,
      stdout=False,
    )

  def _service_traefik_router_names(self, service: dict[str, Any]) -> set[str]:
    labels = get_traefik_http_labels(service)
    routers: set[str] = set()

    for label in labels:
      key = str(label)

      if not key.startswith('traefik.http.routers.'):
        continue

      parts = key.split('.')
      if len(parts) >= 5:
        routers.add(parts[3])

    return routers

  def _service_traefik_target_ports(self, labels: dict[str, Any]) -> dict[str, int]:
    ports: dict[str, int] = {}

    for label, value in labels.items():
      match = self._TRAEFIK_SERVICE_PORT_RE.match(str(label))
      if not match:
        continue

      raw_port = str(value).strip()
      if raw_port.isdigit():
        ports[match.group(1)] = int(raw_port)

    return ports

  def _strip_traefik_labels(self, service: dict[str, Any]) -> None:
    if 'labels' in service and isinstance(service['labels'], dict):
      filtered = {
        key: value
        for key, value in service['labels'].items()
        if not str(key).startswith('traefik.')
      }
      if filtered:
        service['labels'] = filtered
      else:
        service.pop('labels', None)

    deploy_cfg = service.get('deploy')
    if isinstance(deploy_cfg, dict) and isinstance(deploy_cfg.get('labels'), dict):
      filtered = {
        key: value
        for key, value in deploy_cfg['labels'].items()
        if not str(key).startswith('traefik.')
      }
      if filtered:
        deploy_cfg['labels'] = filtered
      else:
        deploy_cfg.pop('labels', None)

  def _ensure_random_host_port_binding(self, service: dict[str, Any], target_port: int) -> None:
    ports = service.get('ports', [])
    filtered_ports: list[Any] = []

    if isinstance(ports, list):
      for entry in ports:
        if isinstance(entry, str):
          candidate = entry.split(':')[-1].split('/')[0]
          if candidate.isdigit() and int(candidate) == target_port:
            continue
          filtered_ports.append(entry)
        elif isinstance(entry, dict):
          candidate = entry.get('target')
          if isinstance(candidate, int) and candidate == target_port:
            continue
          filtered_ports.append(entry)

    # Compose syntax "<target_port>" publishes on a random host port.
    filtered_ports.append(str(target_port))
    service['ports'] = filtered_ports

  def _parse_published_host_port(self, output: str) -> int | None:
    line = output.strip().splitlines()[0].strip() if output.strip() else ''
    if not line:
      return None

    parsed = urllib_parse.urlsplit(f'tcp://{line}')
    if parsed.port is not None:
      return parsed.port

    match = re.search(r':(\d+)\s*$', line)
    if match:
      return int(match.group(1))

    return None

  def _resolve_node_ip(self, nodename: str) -> str:
    for node in self.client.nodes.list():
      attrs = getattr(node, 'attrs', {}) or {}
      hostname = attrs.get('Description', {}).get('Hostname')

      if hostname == nodename:
        ip_address = attrs.get('Status', {}).get('Addr')

        if isinstance(ip_address, str) and ip_address.strip():
          return ip_address.strip()

    return nodename

  def _extract_traefik_service_specs(self) -> dict[str, dict[str, Any]]:
    extracted: dict[str, dict[str, Any]] = {}

    for service_name, service in self.config.get('services', {}).items():
      if not isinstance(service, dict):
        continue

      labels = get_traefik_http_labels(service)
      traefik_labels = {
        key: value for key, value in labels.items() if str(key).startswith('traefik.')
      }

      if not traefik_labels:
        continue

      router_names = self._service_traefik_router_names({'labels': traefik_labels})
      if not router_names:
        logger.info(
          'Compose service %s has Traefik labels but no router definition; skipping route extraction.',
          service_name,
        )
        self._strip_traefik_labels(service)
        continue

      target_ports = self._service_traefik_target_ports(traefik_labels)
      for target_port in sorted(set(target_ports.values())):
        self._ensure_random_host_port_binding(service, target_port)
        logger.info(
          'Compose service %s: mapped Traefik target port %s to random host port binding.',
          service_name,
          target_port,
        )

      extracted[service_name] = {
        'labels': traefik_labels,
        'target_ports': target_ports,
      }
      self._strip_traefik_labels(service)
      logger.info(
        'Compose service %s: extracted and removed Traefik labels from service config.',
        service_name,
      )

    return extracted

  def prepare_traefik_publication(self) -> None:
    if self._traefik_prepared:
      return

    self._traefik_service_specs = self._extract_traefik_service_specs()
    self._traefik_prepared = True

    if self._traefik_service_specs:
      logger.info(
        'Prepared %s compose service(s) with extracted Traefik labels for provider publication.',
        len(self._traefik_service_specs),
      )

  def build(self) -> None:
    self.prepare_traefik_publication()
    super().build()

  def push(self) -> None:
    self.prepare_traefik_publication()
    super().push()

  def _traefik_provider_endpoints(self, nodename: str, node_ip: str) -> list[str]:
    endpoints: list[str] = []

    configured = os.getenv('SETO_TRAEFIK_HTTP_ENDPOINT', DEFAULT_TRAEFIK_HTTP_ENDPOINT)
    if configured:
      endpoints.append(configured)

    if node_ip:
      endpoints.append(f'http://{node_ip}:{self._TRAEFIK_PROVIDER_PORT}/traefik')

    if nodename:
      endpoints.append(f'http://{nodename}:{self._TRAEFIK_PROVIDER_PORT}/traefik')

    endpoints.append(f'http://localhost:{self._TRAEFIK_PROVIDER_PORT}/traefik')

    deduped: list[str] = []
    seen: set[str] = set()

    for endpoint in endpoints:
      if endpoint not in seen:
        seen.add(endpoint)
        deduped.append(endpoint)

    return deduped

  def _sync_traefik_routes_from_node(
    self,
    nodename: str,
    routes: dict[str, dict[str, Any]],
    endpoints: list[str],
  ) -> bool:
    payload = json.dumps(
      {
        'http': {
          'routers': {
            name: config
            for route in routes.values()
            for name, config in route.get('http', {}).get('routers', {}).items()
          },
          'services': {
            name: config
            for route in routes.values()
            for name, config in route.get('http', {}).get('services', {}).items()
          },
        }
      }
    )

    script = (
      'import json,sys\\n'
      'from urllib import request as r,error as e\\n'
      'payload=json.loads(sys.argv[1])\\n'
      'endpoints=json.loads(sys.argv[2])\\n'
      'body=json.dumps(payload).encode("utf-8")\\n'
      'ok=False\\n'
      'for endpoint in endpoints:\\n'
      '  req=r.Request(endpoint,data=body,headers={"Content-Type":"application/json"},method="PUT")\\n'
      '  try:\\n'
      '    with r.urlopen(req,timeout=5) as res:  # type: ignore[arg-type]\\n'
      '      if 200 <= res.getcode() < 300:\\n'
      '        ok=True\\n'
      '        break\\n'
      '  except (e.URLError,OSError,ValueError):\\n'
      '    pass\\n'
      'print("1" if ok else "0")\\n'
    )

    command = f'python3 -c {shlex.quote(script)} {shlex.quote(payload)} {shlex.quote(json.dumps(endpoints))}'
    output = self._node_capture(command, hostname=nodename)

    return output.strip().splitlines()[-1:] == ['1']

  def pull(self) -> None:
    self.prepare_traefik_publication()

    for nodename in self.nodenames:
      self._exec(self.compose_command('pull'), nodename)

  def info(self) -> None:
    self.prepare_traefik_publication()

    if self.driver.storage_node:
      command = self.compose_command('config')
    else:
      command = self.compose_command('config --no-env-resolution --skip-interpolation')

    for nodename in self.nodenames:
      self._exec(
        command,
        nodename,
        resolved_config=re.sub(NODE_HOSTNAME_RE, nodename, self.resolved_config),
      )

  def deploy(self) -> None:
    self.prepare_traefik_publication()
    nodenames = self.nodenames

    for service in self.config.get('services', {}).values():
      if 'build' in service:
        del service['build']

    for nodename in nodenames:
      logger.info('Deploying on %s...', nodename)
      self._exec(
        self.compose_command('up -d --remove-orphans'),
        nodename,
        resolved_config=re.sub(NODE_HOSTNAME_RE, nodename, self.resolved_config),
      )

      # Keep the first random host port assigned by Docker and pin it in the
      # effective compose object so future manual restarts/recreates keep using
      # the same published port.
      if len(nodenames) == 1 and self._pin_current_published_ports(nodename):
        self._exec(
          self.compose_command('up -d --remove-orphans'),
          nodename,
          resolved_config=re.sub(NODE_HOSTNAME_RE, nodename, self.resolved_config),
        )

      self._publish_traefik_routes(nodename)

  def _replace_target_port_binding(
    self, service: dict[str, Any], *, target_port: int, published_port: int
  ) -> bool:
    ports = service.get('ports', [])
    filtered_ports: list[Any] = []
    replaced = False

    if isinstance(ports, list):
      for entry in ports:
        if isinstance(entry, str):
          candidate = entry.split(':')[-1].split('/')[0]
          if candidate.isdigit() and int(candidate) == target_port:
            replaced = True
            continue
          filtered_ports.append(entry)
        elif isinstance(entry, dict):
          candidate = entry.get('target')
          if isinstance(candidate, int) and candidate == target_port:
            replaced = True
            continue
          filtered_ports.append(entry)

    explicit_binding = f'{published_port}:{target_port}'
    if explicit_binding not in filtered_ports:
      filtered_ports.append(explicit_binding)

    service['ports'] = filtered_ports

    return replaced

  def _pin_current_published_ports(self, nodename: str) -> bool:
    changed = False

    for service_name, spec in self._traefik_service_specs.items():
      target_ports = spec.get('target_ports', {})
      service = self.config.get('services', {}).get(service_name)

      if not isinstance(service, dict) or not isinstance(target_ports, dict):
        continue

      for target_port in sorted(set(target_ports.values())):
        if not isinstance(target_port, int):
          continue

        port_output = self._exec_output(
          self.compose_command(f'port {service_name} {target_port}'),
          nodename,
        )
        host_port = self._parse_published_host_port(port_output)

        if host_port is None:
          continue

        if self._replace_target_port_binding(
          service, target_port=target_port, published_port=host_port
        ):
          changed = True
          logger.info(
            'Compose service %s: pinned published host port %s for target %s.',
            service_name,
            host_port,
            target_port,
          )

    return changed

  def _publish_traefik_routes(self, nodename: str) -> None:
    routes: dict[str, dict[str, Any]] = {}
    node_ip = self._resolve_node_ip(nodename)

    logger.info('Publishing compose Traefik routes from node %s (IPv4: %s).', nodename, node_ip)

    for service_name, spec in self._traefik_service_specs.items():
      labels = spec.get('labels', {})
      target_ports = spec.get('target_ports', {})

      if not isinstance(labels, dict):
        continue

      service_external_ports: dict[str, int] = {}
      resolved_by_target_port: dict[int, int] = {}

      if isinstance(target_ports, dict):
        unresolved = False

        for traefik_service, target_port in target_ports.items():
          if not isinstance(target_port, int):
            continue

          host_port = resolved_by_target_port.get(target_port)
          if host_port is None:
            port_output = self._exec_output(
              self.compose_command(f'port {service_name} {target_port}'),
              nodename,
            )
            host_port = self._parse_published_host_port(port_output)

            if host_port is None:
              logger.warning(
                'Compose service %s: unable to resolve published host port for target %s; skipping route publish.',
                service_name,
                target_port,
              )
              unresolved = True
              break

            resolved_by_target_port[target_port] = host_port

          service_external_ports[traefik_service] = host_port
          logger.info(
            'Compose service %s: resolved load balancer redirect %s:%s -> service:%s.',
            service_name,
            node_ip,
            host_port,
            target_port,
          )

        if unresolved:
          continue

      route = extract_traefik_route(
        service_name,
        {'labels': labels},
        hostname=node_ip,
        port=next(iter(service_external_ports.values()), None),
        service_external_ports=service_external_ports,
      )

      if route:
        route_id = route.get('route_id', service_name)
        routes[f'{self.driver.stack_id}-{route_id}'] = route
        logger.info(
          'Compose service %s: route %s prepared for seto-provider.', service_name, route_id
        )

    if not routes:
      logger.info('No compose Traefik routes to publish for node %s.', nodename)
      return

    pruned = prune_stale_traefik_routes(routes)
    endpoints = self._traefik_provider_endpoints(nodename, node_ip)

    for endpoint in endpoints:
      if sync_traefik_http_registry(pruned, endpoint=endpoint):
        return

    if self._sync_traefik_routes_from_node(nodename, pruned, endpoints):
      return

    logger.warning(
      'Unable to publish Traefik routes for stack %s via endpoints: %s',
      self.driver.stack_id,
      ', '.join(endpoints),
    )

  def _remove_traefik_routes(self) -> None:
    routes: dict[str, dict[str, Any]] = {}
    specs = self._traefik_service_specs

    if not specs:
      for service_name, service in self.config.get('services', {}).items():
        if not isinstance(service, dict):
          continue

        labels = get_traefik_http_labels(service)
        traefik_labels = {
          key: value for key, value in labels.items() if str(key).startswith('traefik.')
        }

        if traefik_labels:
          specs[service_name] = {'labels': traefik_labels, 'target_ports': {}}

    for service_name, spec in specs.items():
      labels = spec.get('labels', {})
      if not isinstance(labels, dict):
        continue

      route = extract_traefik_route(
        service_name,
        {'labels': labels},
        hostname='localhost',
        port=None,
      )
      if route:
        routes[f'{self.driver.stack_id}-{route.get("route_id", service_name)}'] = route

    if not routes:
      return

    for route_id in routes:
      remove_traefik_http_registry_route(route_id)

  def ps(self) -> None:
    for nodename in self.nodenames:
      logger.info('--- Services on %s ---', nodename)
      self._exec(self.compose_command('ps'), nodename)

  def logs(self) -> None:
    for nodename in self.nodenames:
      logger.info('--- Logs on %s ---', nodename)
      self._exec(self.compose_command('logs -n 10'), nodename)

  def down(self) -> None:
    for nodename in self.nodenames:
      logger.info('Removing stack from %s...', nodename)
      self._exec(self.compose_command('down'), nodename)

    self._remove_traefik_routes()


class DockerSwarm(Docker):
  """Swarm orchestration.

  Swarm commands are always issued against the Swarm *manager*: its Docker
  context when ``seto setup`` registered one, otherwise the local Docker daemon
  (the historical requirement that the CLI runs on the manager).
  """

  def pull(self) -> None:
    pass

  def info(self) -> None:
    if self.driver.storage_node:
      command = self.contexts.manager_cli('stack config -c -')
    else:
      command = self.contexts.manager_cli('stack config -c - --skip-interpolation')

    self.shell.run(command=command, input=self.resolved_config)

  def deploy(self) -> None:
    for service in self.config.get('services', {}).values():
      if 'build' in service:
        del service['build']

    self.shell.run(
      command=self.contexts.local_cli(
        context=None,
        subcommand=f'stack deploy --prune --detach=true --resolve-image=always -c - {self.driver.stack_id}',
      ),
      input=self.resolved_config,
    )

  def ps(self) -> None:
    self.shell.run(
      command=self.contexts.manager_cli(f'stack ps --no-trunc {self.driver.stack_id}'),
      stderr=False,
      check=False,
    )
    self.shell.run(
      command=self.contexts.manager_cli(f'stack services {self.driver.stack_id}'),
      stderr=False,
      check=False,
    )

  def logs(self) -> None:
    for service in self.config.get('services', {}).keys():
      self.shell.run(
        command=self.contexts.manager_cli(f'service logs -n 10 {self.driver.stack_id}_{service}'),
        stderr=False,
        check=False,
      )

  def down(self) -> None:
    self.shell.run(command=self.contexts.manager_cli(f'stack rm {self.driver.stack_id}'))
