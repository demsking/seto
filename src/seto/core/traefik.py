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
import json
import os
import time
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

DEFAULT_TRAEFIK_HTTP_ENDPOINT = 'http://seto-provider:8080/traefik'
DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS = 30


def candidate_traefik_http_endpoints(endpoint: str) -> list[str]:
  """Return endpoint candidates, including localhost fallbacks.

  Compose route publication can be executed from outside Docker networks where
  DNS aliases such as `seto-provider` are not resolvable. In that context,
  retrying the same endpoint on localhost helps reach the published provider
  port on the host.
  """
  candidates = [endpoint]

  try:
    parsed = urlsplit(endpoint)
  except ValueError:
    return candidates

  hostname = parsed.hostname
  if not hostname:
    return candidates

  lowered = hostname.lower()
  if lowered in {'localhost', '127.0.0.1'}:
    return candidates

  netloc_suffix = ''
  if parsed.port is not None:
    netloc_suffix = f':{parsed.port}'

  for fallback_host in ('localhost', '127.0.0.1'):
    fallback_netloc = f'{fallback_host}{netloc_suffix}'
    fallback_endpoint = urlunsplit(
      (parsed.scheme, fallback_netloc, parsed.path, parsed.query, parsed.fragment)
    )
    if fallback_endpoint not in candidates:
      candidates.append(fallback_endpoint)

  return candidates


def deep_set(dct: dict[str, Any], keys: str, value: Any):
  """
  Helper function to set a nested dictionary value using a dot-separated key.
  This handles array notation like 'rewrites[0]' and converts plural-named keys to arrays.

  Args:
    dct: The dictionary to modify.
    keys: A dot-separated string representing the nested keys.
    value: The value to set at the nested location.
  """
  keys_list = keys.replace('[', '.').replace(']', '').split('.')
  current = dct

  for key in keys_list[:-1]:
    if key.isdigit():  # Convert to list if the key is an index
      key = int(key)

      if not isinstance(current, list):
        current = []

      # Extend the list if the index doesn't exist yet
      while len(current) <= key:
        current.append({})

      current = current[key]  # type: ignore
    else:
      if isinstance(current, list):  # Handle case where current is mistakenly a list
        current = current[-1]  # type: ignore

      next_value = current.get(key)

      if not isinstance(next_value, dict):
        next_value = {}
        current[key] = next_value

      current = next_value

  # Handle the last key separately
  final_key = keys_list[-1]

  # Convert plural-named parameters to arrays (e.g. 'middlewares', 'rewrites')
  if final_key.endswith('s') and isinstance(value, str):
    value = [item.strip() for item in value.split(',')]

  if final_key.isdigit():  # Handle if the final key is an array index
    final_key = int(final_key)
    if not isinstance(current, list):
      current = []
    while len(current) <= final_key:
      current.append({})
    current[final_key] = value
  else:
    current[final_key] = value


def convert_middlewares_to_dict(labels: dict[str, str]) -> dict:
  """
  Convert all Traefik middleware labels to JSON format, handling arrays for plural-named keys
  and deeply nested fields.

  Args:
    labels: A dictionary of Docker labels.

  Returns:
    A JSON string representing the middleware configurations.
  """
  middlewares = {}

  for label, value in labels.items():
    if label.startswith('traefik.http.middlewares.'):
      parts = label.split('.')

      if len(parts) > 3:
        middleware_name = parts[3]
        middleware_key = '.'.join(parts[4:])

        if middleware_name not in middlewares:
          middlewares[middleware_name] = {}

        # Use the deep_set helper to handle nested keys
        deep_set(middlewares[middleware_name], middleware_key, value)

  return middlewares


def get_traefik_http_labels(service: dict[str, Any]) -> dict[str, Any]:
  labels: dict[str, Any] = {}

  if not isinstance(service, dict):
    return labels

  containers: list[Any] = [service.get('labels', {})]
  deploy_cfg = service.get('deploy', {})
  if isinstance(deploy_cfg, dict):
    containers.append(deploy_cfg.get('labels', {}))

  for container in containers:
    if isinstance(container, dict):
      labels.update(container)

  return labels


def is_traefik_service(service: dict[str, Any]) -> bool:
  labels = get_traefik_http_labels(service)

  return any(str(key).startswith('traefik.http.') for key in labels)


def extract_traefik_route(
  service_name: str,
  service: dict[str, Any],
  *,
  hostname: str,
  port: int | None = None,
  external_port: int | None = None,
  service_external_ports: dict[str, int] | None = None,
) -> dict[str, Any] | None:
  if not isinstance(service, dict):
    return None

  labels = get_traefik_http_labels(service)

  if not labels:
    return None

  routers: dict[str, Any] = {}
  services: dict[str, Any] = {}
  route_service_name = service_name
  route_id = service_name

  for label, value in labels.items():
    if not str(label).startswith('traefik.http.'):
      continue

    parts = str(label).split('.')

    if len(parts) < 5:
      continue

    if parts[0:3] == ['traefik', 'http', 'routers']:
      router_name = parts[3]
      router_key = '.'.join(parts[4:])

      if router_name not in routers:
        routers[router_name] = {}

      deep_set(routers[router_name], router_key, str(value))
      route_service_name = str(routers[router_name].get('service', route_service_name))
      route_id = router_name
    elif parts[0:3] == ['traefik', 'http', 'services']:
      service_name_key = parts[3]
      service_key = '.'.join(parts[4:])

      if service_name_key not in services:
        services[service_name_key] = {}

      deep_set(services[service_name_key], service_key, str(value))

  if not routers:
    return None

  for router_name, config in routers.items():
    if 'entrypoints' in config and 'entryPoints' not in config:
      config['entryPoints'] = config.pop('entrypoints')

    tls_config = config.get('tls')
    if isinstance(tls_config, str):
      lowered = tls_config.strip().lower()
      if lowered == 'true':
        config['tls'] = True
      elif lowered == 'false':
        config['tls'] = False
    elif (
      isinstance(tls_config, dict)
      and 'certresolver' in tls_config
      and 'certResolver' not in tls_config
    ):
      tls_config['certResolver'] = tls_config.pop('certresolver')

    config.setdefault('service', route_service_name)
    config.setdefault('entryPoints', ['web'])

    if 'rule' not in config:
      config['rule'] = f'Host(`{router_name}.local`)'

  if not services:
    services[route_service_name] = {
      'loadBalancer': {
        'servers': [
          {
            'url': f'http://{hostname}:{port or 80}',
          }
        ],
      }
    }

  # If the service-specific labels define a target port, use it as the backend port.
  for service_id, service_config in services.items():
    if 'loadbalancer' in service_config and 'loadBalancer' not in service_config:
      service_config['loadBalancer'] = service_config.pop('loadbalancer')

    load_balancer = service_config.get('loadBalancer', {})

    if (
      isinstance(load_balancer, dict)
      and 'server' in load_balancer
      and 'servers' not in load_balancer
    ):
      server = load_balancer.pop('server')
      if isinstance(server, dict):
        load_balancer['servers'] = [server]

    server = load_balancer.get('servers', [])

    if isinstance(server, list) and server and isinstance(server[0], dict):
      server[0].setdefault('url', f'http://{hostname}:{port or 80}')

      if service_external_ports and service_id in service_external_ports:
        server[0]['url'] = f'http://{hostname}:{service_external_ports[service_id]}'
      elif external_port is not None:
        server[0]['url'] = f'http://{hostname}:{external_port}'
      elif 'port' in server[0]:
        server[0]['url'] = f'http://{hostname}:{server[0]["port"]}'

  dynamic = {
    'http': {
      'routers': routers,
      'services': services,
    },
    'last_seen': int(time.time()),
    'route_id': route_id,
  }

  return dynamic


def prune_stale_traefik_routes(
  routes: dict[str, dict[str, Any]], *, ttl_seconds: int = DEFAULT_TRAEFIK_ROUTE_TTL_SECONDS
) -> dict[str, dict[str, Any]]:
  now = int(time.time())
  cleaned: dict[str, dict[str, Any]] = {}

  for route_id, route in routes.items():
    last_seen = int(route.get('last_seen', 0))

    if now - last_seen <= ttl_seconds:
      cleaned[route_id] = route

  return cleaned


def build_traefik_http_provider_state(routes: dict[str, dict[str, Any]]) -> dict[str, Any]:
  http_def: dict[str, Any] = {'routers': {}, 'services': {}}

  for route in routes.values():
    http_config = route.get('http', {})

    if isinstance(http_config, dict):
      for name, config in http_config.get('routers', {}).items():
        http_def['routers'][name] = config

      for name, config in http_config.get('services', {}).items():
        http_def['services'][name] = config

  return {'http': http_def}


def merge_traefik_provider_state(
  current: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
  merged: dict[str, Any] = dict(current)

  for key, value in incoming.items():
    if key == 'http' and isinstance(value, dict):
      existing_http = merged.get('http', {})
      if not isinstance(existing_http, dict):
        existing_http = {}

      combined_http: dict[str, Any] = dict(existing_http)
      for nested_key, nested_value in value.items():
        if nested_key in {'routers', 'services'} and isinstance(nested_value, dict):
          current_section = dict(combined_http.get(nested_key, {}))
          current_section.update(nested_value)
          combined_http[nested_key] = current_section
        else:
          combined_http[nested_key] = nested_value
      merged['http'] = combined_http
    elif key == '_seto_registry' and isinstance(value, dict):
      existing_registry = merged.get('_seto_registry', {})
      if not isinstance(existing_registry, dict):
        existing_registry = {}

      combined_registry: dict[str, Any] = dict(existing_registry)
      for registry_key, registry_value in value.items():
        if registry_key == 'agents' and isinstance(registry_value, dict):
          agents = dict(combined_registry.get('agents', {}))
          agents.update(registry_value)
          combined_registry['agents'] = agents
        else:
          combined_registry[registry_key] = registry_value
      merged['_seto_registry'] = combined_registry
    else:
      merged[key] = value

  return merged


def sync_traefik_http_registry(
  routes: dict[str, dict[str, Any]], *, endpoint: str | None = None
) -> bool:
  endpoint = endpoint or os.getenv('SETO_TRAEFIK_HTTP_ENDPOINT', DEFAULT_TRAEFIK_HTTP_ENDPOINT)

  if not endpoint:
    return False

  payload = build_traefik_http_provider_state(routes)

  for candidate in candidate_traefik_http_endpoints(endpoint):
    request = urllib_request.Request(
      candidate,
      data=json.dumps(payload).encode('utf-8'),
      headers={'Content-Type': 'application/json'},
      method='PUT',
    )

    try:
      with urllib_request.urlopen(request, timeout=5) as response:  # type: ignore[arg-type]
        if 200 <= response.getcode() < 300:
          return True
    except (urllib_error.URLError, OSError, ValueError):
      continue

  return False


def remove_traefik_http_registry_route(route_id: str, *, endpoint: str | None = None) -> bool:
  endpoint = endpoint or os.getenv('SETO_TRAEFIK_HTTP_ENDPOINT', DEFAULT_TRAEFIK_HTTP_ENDPOINT)

  if not endpoint:
    return False

  for candidate in candidate_traefik_http_endpoints(endpoint):
    target = f'{candidate.rstrip("/")}/{route_id}'
    request = urllib_request.Request(target, method='DELETE')

    try:
      with urllib_request.urlopen(request, timeout=5) as response:  # type: ignore[arg-type]
        if 200 <= response.getcode() < 300:
          return True
    except (urllib_error.URLError, OSError, ValueError):
      continue

  return False
