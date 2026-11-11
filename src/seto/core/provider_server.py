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
"""Traefik HTTP provider served by the ``seto-provider`` service.

Ṣeto ships this module to the `seto-provider` container and runs it as a
standalone script (see ``seto.commands.deploy.seto_provider_command``). It keeps
the Traefik dynamic configuration published by every Compose deployment in
memory and exposes it on ``/traefik``, which is what a Traefik HTTP provider
polls.

Route expiry is the point of this service: a route is only served while it is
still alive. A route is dropped as soon as its backend stops answering (the
target container was stopped, for instance) or once it has been registered for
longer than the TTL without being renewed.

Keep this module dependency-free: it runs inside a bare ``python:3.12-alpine``
image with no third-party package installed.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

DEFAULT_TTL_SECONDS = 5
PROBE_TIMEOUT_SECONDS = 2
LISTEN_ADDRESS = ('0.0.0.0', 8080)


def resolve_ttl(argv: list[str]) -> int:
  """Return the route TTL in seconds from the command-line arguments."""
  if len(argv) > 0:
    try:
      return max(1, int(argv[0]))
    except (TypeError, ValueError):
      pass

  return DEFAULT_TTL_SECONDS


def sweep_interval(ttl: int) -> float:
  """Return how often expired routes are collected, well within a TTL."""
  return max(0.5, min(1.0, ttl / 5.0))


def route_server_urls(route: dict[str, Any]) -> list[str]:
  """Return the backend server URLs a route load balances to."""
  urls: list[str] = []
  services = route.get('http', {}).get('services', {})

  if not isinstance(services, dict):
    return urls

  for config in services.values():
    if not isinstance(config, dict):
      continue

    load_balancer = config.get('loadBalancer', {})

    if not isinstance(load_balancer, dict):
      continue

    servers = load_balancer.get('servers', [])

    if not isinstance(servers, list):
      continue

    for server in servers:
      url = server.get('url') if isinstance(server, dict) else None

      if isinstance(url, str) and url.strip():
        urls.append(url.strip())

  return urls


def probe_route(route: dict[str, Any]) -> bool:
  """Return ``True`` when at least one backend of ``route`` still answers.

  Any HTTP status is treated as alive: the only question asked here is whether
  the host and port still accept connections.
  """
  for url in route_server_urls(route):
    request = urllib_request.Request(url, method='HEAD')

    try:
      with urllib_request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS):  # type: ignore[arg-type]
        return True
    except urllib_error.HTTPError:
      return True
    except (urllib_error.URLError, OSError, ValueError):
      continue

  return False


class RouteRegistry:
  """In-memory store of the published routes, with TTL and liveness expiry."""

  def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, *, probe: Any = probe_route) -> None:
    self.ttl_seconds = ttl_seconds
    self._probe = probe
    self._routes: dict[str, dict[str, Any]] = {}
    self._lock = threading.RLock()

  def __len__(self) -> int:
    return len(self._routes)

  def register(self, route_id: str, route: dict[str, Any]) -> None:
    """Store ``route`` under ``route_id``, restarting its TTL only once."""
    with self._lock:
      previous = self._routes.get(route_id, {})
      self._routes[route_id] = {
        'route': normalize_route(route),
        'probe': bool(route.get('probe', True)),
        'registered_at': previous.get('registered_at', time.monotonic()),
      }

  def remove(self, route_id: str) -> bool:
    with self._lock:
      return self._routes.pop(route_id, None) is not None

  def drop_expired(self, now: float | None = None) -> list[str]:
    """Drop the routes that outlived the TTL or whose backend stopped answering."""
    reference = time.monotonic() if now is None else now
    expired: list[str] = []

    with self._lock:
      for route_id, record in list(self._routes.items()):
        if reference - record['registered_at'] > self.ttl_seconds:
          expired.append(route_id)
        elif record['probe'] and not self._probe(record['route']):
          expired.append(route_id)

      for route_id in expired:
        self._routes.pop(route_id, None)

    return expired

  def http_state(self) -> dict[str, Any]:
    """Return the Traefik dynamic configuration built from the live routes."""
    http: dict[str, Any] = {'routers': {}, 'services': {}}

    with self._lock:
      for record in self._routes.values():
        route = record['route']

        for section in ('routers', 'services'):
          entries = route.get('http', {}).get(section, {})

          if isinstance(entries, dict):
            http[section].update(entries)

    return {'http': http}

  def registry_state(self) -> dict[str, Any]:
    """Return the route bookkeeping exposed to Ṣeto under ``_seto_registry``."""
    with self._lock:
      records = {
        route_id: dict(record['route'], route_id=record['route'].get('route_id', route_id))
        for route_id, record in self._routes.items()
      }

    return {'_seto_registry': {'routes': records}}

  def state(self) -> dict[str, Any]:
    return {**self.http_state(), **self.registry_state()}


def normalize_route(payload: dict[str, Any]) -> dict[str, Any]:
  """Accept the historical payload shapes and return ``{'http': {...}}``."""
  if 'http' not in payload and any(key in payload for key in ('routers', 'services')):
    payload = {'http': payload}

  if isinstance(payload.get('http'), dict):
    nested = payload['http'].get('http')

    if isinstance(nested, dict):
      payload['http'] = nested

    return payload

  return {'http': {'routers': {}, 'services': {}}}


class ProviderHandler(BaseHTTPRequestHandler):
  registry: RouteRegistry

  def handle(self) -> None:
    try:
      super().handle()
    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
      pass

  def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
    return

  def _send(self, code: int, payload: Any | None = None) -> None:
    self.send_response(code)

    if payload is None:
      self.send_header('Content-Length', '0')
      self.end_headers()
      return

    body = json.dumps(payload).encode('utf-8')
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def do_GET(self) -> None:
    normalized = self.path.rstrip('/')

    if normalized == '/traefik':
      self.registry.drop_expired()
      self._send(200, self.registry.state())
      return

    if normalized == '/health':
      self._send(200, {'ttl': self.registry.ttl_seconds, 'routes': len(self.registry)})
      return

    self._send(404, {'error': 'not found'})

  def do_PUT(self) -> None:
    try:
      length = int(self.headers.get('Content-Length', '0'))
      raw = self.rfile.read(length) if length else b'{}'
      payload = json.loads(raw.decode('utf-8'))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
      self._send(400, {'error': 'invalid json'})
      return

    if not isinstance(payload, dict):
      self._send(400, {'error': 'invalid payload'})
      return

    normalized = self.path.rstrip('/')

    if normalized.startswith('/traefik/'):
      route_id = normalized[len('/traefik/'):].strip('/')

      if not route_id:
        self._send(400, {'error': 'invalid route'})
        return

      self.registry.register(route_id, payload)
      self._send(204)
      return

    if normalized != '/traefik':
      self._send(404, {'error': 'not found'})
      return

    for route_id, route in group_routes(payload).items():
      self.registry.register(route_id, route)

    self._send(204)

  def do_DELETE(self) -> None:
    if not self.path.startswith('/traefik/'):
      self._send(404, {'error': 'not found'})
      return

    route_id = self.path[len('/traefik/'):].strip('/')
    self.registry.remove(route_id)
    self._send(204)


def group_routes(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
  """Split a bare ``/traefik`` payload into its per-route configurations.

  A payload carrying a ``_seto_registry`` block is split by registry id. A bare
  ``http`` block cannot be attributed to any specific route id, so it is stored
  under the empty id: it is served, but it is never expired on behalf of a stack.
  """
  registry = payload.get('_seto_registry', {})
  records = registry.get('routes', {}) if isinstance(registry, dict) else {}

  if isinstance(records, dict) and records:
    return {
      str(route_id): record for route_id, record in records.items() if isinstance(record, dict)
    }

  http = payload.get('http', {})

  return {'': {'http': http}} if isinstance(http, dict) else {}


def start_reaper(registry: RouteRegistry, interval: float) -> threading.Thread:
  """Run :meth:`RouteRegistry.drop_expired` periodically in the background."""

  def _loop() -> None:
    while True:
      time.sleep(interval)

      try:
        registry.drop_expired()
      except Exception:  # pylint: disable=broad-except
        pass

  thread = threading.Thread(target=_loop, daemon=True)
  thread.start()

  return thread


def build_server(
  ttl_seconds: int = DEFAULT_TTL_SECONDS, address: tuple[str, int] = LISTEN_ADDRESS
) -> ThreadingHTTPServer:
  """Create the HTTP server serving the registry on ``address``."""
  registry = RouteRegistry(ttl_seconds)
  handler = type('BoundProviderHandler', (ProviderHandler,), {'registry': registry})

  return ThreadingHTTPServer(address, handler)


def main(argv: list[str] | None = None) -> None:
  args = sys.argv[1:] if argv is None else argv
  ttl = resolve_ttl(args)
  server = build_server(ttl)
  start_reaper(server.RequestHandlerClass.registry, sweep_interval(ttl))  # type: ignore[attr-defined]
  server.serve_forever()


if __name__ == '__main__':
  main()
