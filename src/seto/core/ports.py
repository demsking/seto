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
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_PORT_CACHE_PATH = os.path.join('.seto', 'ports.json')
PORT_CACHE_VERSION = 1


class PortCache:
  """Persisted host port reservations for Compose service target ports.

  Services that expose a Traefik target port without an explicit host port are
  published by Docker Compose on a random host port. The random port is only
  known once the container is up, so a redeployment would otherwise pick a new
  one and invalidate the route Traefik already points to. Reserving the first
  assigned port in ``.seto/ports.json`` keeps the published binding stable
  across ``seto deploy`` runs.
  """

  def __init__(self, path: str = DEFAULT_PORT_CACHE_PATH) -> None:
    self.path = path
    self._ports: dict[str, int] | None = None
    self._dirty = False

  @staticmethod
  def key(project: str, service: str, target_port: int) -> str:
    """Return the stable cache key of a service target port."""
    return f'{project}/{service}/{target_port}'

  def _entries(self) -> dict[str, int]:
    if self._ports is None:
      self._ports = self._read()

    return self._ports

  def _read(self) -> dict[str, int]:
    if not os.path.exists(self.path):
      return {}

    try:
      with open(self.path, encoding='utf-8') as file:
        data = json.load(file)
    except (OSError, ValueError) as error:
      logger.warning("Ignoring unreadable port cache '%s': %s", self.path, error)
      return {}

    ports = data.get('ports') if isinstance(data, dict) else None

    if not isinstance(ports, dict):
      return {}

    return {
      str(name): port
      for name, port in ports.items()
      if isinstance(port, int) and not isinstance(port, bool)
    }

  def get(self, *, project: str, service: str, target_port: int) -> int | None:
    """Return the reserved host port of a service target port, if any."""
    return self._entries().get(self.key(project, service, target_port))

  def reserve(self, *, project: str, service: str, target_port: int, host_port: int) -> bool:
    """Reserve ``host_port`` for a service target port; report whether it changed."""
    ports = self._entries()
    name = self.key(project, service, target_port)

    if ports.get(name) == host_port:
      return False

    ports[name] = host_port
    self._dirty = True

    return True

  def flush(self) -> None:
    """Write pending reservations to disk; a no-op when nothing changed."""
    if not self._dirty or self._ports is None:
      return

    directory = os.path.dirname(self.path)

    if directory:
      os.makedirs(directory, exist_ok=True)

    payload = {'version': PORT_CACHE_VERSION, 'ports': self._ports}

    try:
      with open(self.path, 'w', encoding='utf-8') as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write('\n')
    except OSError as error:
      logger.warning("Unable to persist the port cache '%s': %s", self.path, error)
      return

    self._dirty = False
