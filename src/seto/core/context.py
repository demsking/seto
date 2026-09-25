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
import hashlib
import json
import logging
import os
import re
import shlex
import socket
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from docker import DockerClient

from ..shells.local import LocalShell
from ..shells.remote import RemoteShell
from .shell import Setting
from .shell import Shell

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join('.seto', 'config.json')
DOCKER_CONTEXT_PREFIX = 'seto-'
LOCAL_DOCKER_CONTEXT = 'default'
LOCAL_DOCKER_ENDPOINT = 'unix:///var/run/docker.sock'
CONFIG_VERSION = 2

LIST_CONTEXTS_COMMAND = "docker context ls --format '{{.Name}}'"

DOCKER_CONTEXT_HINT = (
  "Docker contexts reach remote daemons through the Docker CLI's SSH transport: ensure the node "
  'is reachable with the ssh client (key or agent, as configured for the current user), that the '
  'SSH user is allowed to use Docker, and that the Docker CLI is installed on the node.'
)

_UNSAFE_CONTEXT_CHARS_RE = re.compile(r'[^a-z0-9._-]+')
_CONTEXT_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_HOSTNAME_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9._:-]*[A-Za-z0-9])?$')
_USERNAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


class DockerContextError(RuntimeError):
  """Base error for Docker context provisioning and resolution failures."""


class DockerContextConflictError(DockerContextError):
  """Raised when a Docker context exists with an incompatible configuration."""


class DockerContextUnavailableError(DockerContextError):
  """Raised when a Docker context cannot reach its Docker daemon."""


class NodeContextMissingError(DockerContextError):
  """Raised when a node has no Docker context registered by ``seto setup``."""


class SetoConfigError(RuntimeError):
  """Raised when the persisted ``.seto/config.json`` file cannot be read."""


def docker_cli(context: str | None, subcommand: str) -> str:
  """Build an explicit ``docker`` command targeting a Docker context.

  Docker CLI operations never rely on the user's current context: every call
  targets either a node context or the built-in local ``default`` context.
  """
  if context:
    return f'docker --context {shlex.quote(context)} {subcommand}'

  return f'docker {subcommand}'


def local_docker_endpoint() -> str:
  """Return the Docker endpoint of the host running Ṣeto."""
  configured = os.getenv('DOCKER_HOST', '').strip()

  if configured.startswith(('unix://', 'npipe://')):
    return configured

  return LOCAL_DOCKER_ENDPOINT


def docker_context_name(hostname: str) -> str:
  """Return the deterministic, collision-safe context name of a node.

  The name is derived from the node identity so the same node always maps to
  the same context. Hostnames that are not valid Docker context names get a
  stable digest suffix, so two different hostnames can never collapse into the
  same context name.
  """
  normalized = (hostname or '').strip().lower()

  if not normalized:
    raise DockerContextError('Unable to derive a Docker context name from an empty hostname')

  sanitized = _UNSAFE_CONTEXT_CHARS_RE.sub('-', normalized).strip('.-')

  if sanitized != normalized:
    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:8]
    sanitized = f'{sanitized or "node"}-{digest}'

  name = f'{DOCKER_CONTEXT_PREFIX}{sanitized}'

  if not _CONTEXT_NAME_RE.match(name):
    raise DockerContextError(f"Unable to derive a valid Docker context name from '{hostname}'")

  return name


def docker_endpoint(hostname: str, username: str | None = None, *, local: bool = False) -> str:
  """Return the Docker daemon endpoint of a node.

  Local nodes use the local Docker socket; remote nodes are reached through the
  Docker CLI's native SSH transport, which reuses the operator's SSH keys. The
  Docker daemon is never exposed over TCP.
  """
  if local:
    return local_docker_endpoint()

  clean_hostname, clean_username = validate_node_identity(hostname, username)

  if clean_username is None:
    raise DockerContextError(f"Missing SSH username for node '{clean_hostname}'")

  return f'ssh://{clean_username}@{clean_hostname}'


def validate_context_name(name: str) -> str:
  """Validate a Docker context name and return it unchanged."""
  clean_name = (name or '').strip()

  if not clean_name or not _CONTEXT_NAME_RE.match(clean_name):
    raise DockerContextError(f"Invalid Docker context name '{name}'")

  return clean_name


def validate_node_identity(hostname: str, username: str | None) -> tuple[str, str | None]:
  """Validate a node identity and return its normalized ``(hostname, username)``."""
  clean_hostname = (hostname or '').strip()

  if not clean_hostname or not _HOSTNAME_RE.match(clean_hostname):
    raise DockerContextError(f"Invalid node hostname '{hostname}'")

  clean_username = (username or '').strip() or None

  if clean_username is not None and not _USERNAME_RE.match(clean_username):
    raise DockerContextError(f"Invalid SSH username '{username}' for node '{clean_hostname}'")

  return clean_hostname, clean_username


def build_docker_client(endpoint: str | None = None) -> DockerClient:
  """Build a Docker SDK client bound to an explicit Docker daemon endpoint.

  ``docker.from_env()`` only honors ``DOCKER_HOST`` and the TLS environment
  variables; it never selects a Docker context. Context-backed nodes therefore
  resolve their endpoint explicitly and build the client themselves.
  """
  if not endpoint:
    return DockerClient.from_env()

  if endpoint.startswith('ssh://'):
    # Shell out to the ssh client so SDK authentication matches the Docker CLI.
    return DockerClient(base_url=endpoint, use_ssh_client=True)

  return DockerClient(base_url=endpoint)


def local_setting() -> Setting:
  """Return the setting of the host running Ṣeto."""
  return Setting(
    hostname='localhost',
    username=None,
    password=None,
    local=True,
    ip='127.0.0.1',
  )


@dataclass
class NodeContext:
  """A configured node and the Docker context that reaches its daemon."""

  hostname: str
  username: str | None = None
  ip: str | None = None
  local: bool = False
  docker_context: str | None = None

  @property
  def endpoint(self) -> str:
    return docker_endpoint(self.hostname, self.username, local=self.local)

  @property
  def derived_context(self) -> str:
    return docker_context_name(self.hostname)

  def to_setting(self) -> Setting:
    return Setting(
      hostname=self.hostname,
      username=self.username,
      password=None,
      local=self.local,
      ip=self.ip or self.hostname,
    )

  def to_dict(self) -> dict[str, Any]:
    return {
      'hostname': self.hostname,
      'username': self.username,
      'ip': self.ip,
      'local': self.local,
      'docker_context': self.docker_context,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> 'NodeContext':
    hostname = str(data.get('hostname') or '').strip()

    if not hostname:
      raise SetoConfigError("Missing 'hostname' in the configured node entry")

    username = data.get('username')
    ip = data.get('ip')
    docker_context = data.get('docker_context')

    return cls(
      hostname=hostname,
      username=str(username) if username else None,
      ip=str(ip) if ip else None,
      local=bool(data.get('local', False)),
      docker_context=str(docker_context) if docker_context else None,
    )

  @classmethod
  def from_setting(cls, setting: Setting, *, docker_context: str | None = None) -> 'NodeContext':
    return cls(
      hostname=setting.hostname,
      username=setting.username,
      ip=setting.ip,
      local=setting.local,
      docker_context=docker_context,
    )


@dataclass
class SetoConfig:
  """Persisted Ṣeto configuration (``.seto/config.json``).

  The configuration stores, for every configured node, the Docker context that
  reaches its Docker daemon. Only the context *name* is persisted: endpoints
  are always derived from the node identity, so a changed SSH user or hostname
  is picked up instead of being duplicated as stale state.
  """

  driver: str = 'nfs'
  storage_base_path: str = '/mnt/nfs'
  storage_node: NodeContext | None = None
  nodes: list[NodeContext] = field(default_factory=list)
  version: int = CONFIG_VERSION

  def all_nodes(self) -> list[NodeContext]:
    if self.storage_node is None:
      return list(self.nodes)

    return [self.storage_node, *self.nodes]

  def node_for(self, hostname: str) -> NodeContext | None:
    expected = (hostname or '').strip().lower()

    for node in self.all_nodes():
      if node.hostname.lower() == expected:
        return node

    return None

  def context_for(self, hostname: str) -> str | None:
    node = self.node_for(hostname)

    if node is None:
      return None

    return node.docker_context

  def upsert_node(self, node: NodeContext, *, storage_node: bool = False) -> None:
    if storage_node:
      self.storage_node = node
      return

    for index, existing in enumerate(self.nodes):
      if existing.hostname.lower() == node.hostname.lower():
        self.nodes[index] = node
        return

    self.nodes.append(node)

  def to_dict(self) -> dict[str, Any]:
    return {
      'version': self.version,
      'driver': self.driver,
      'storage_base_path': self.storage_base_path,
      'storage_node': self.storage_node.to_dict() if self.storage_node else None,
      'nodes': [node.to_dict() for node in self.nodes],
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> 'SetoConfig':
    if not isinstance(data, dict):
      raise SetoConfigError('Invalid configuration file: expected a JSON object')

    # Backward compatibility: older configurations stored the storage node
    # under the 'manager' key and never stored client nodes.
    storage_node_data = data.get('storage_node') or data.get('manager')
    nodes_data = data.get('nodes') or []

    if not isinstance(nodes_data, list):
      raise SetoConfigError("Invalid configuration file: 'nodes' must be a list")

    return cls(
      driver=str(data.get('driver', 'nfs')),
      storage_base_path=str(data.get('storage_base_path', '/mnt/nfs')),
      storage_node=NodeContext.from_dict(storage_node_data) if storage_node_data else None,
      nodes=[NodeContext.from_dict(item) for item in nodes_data],
      version=int(data.get('version') or 1),
    )

  @classmethod
  def load(cls, path: str = DEFAULT_CONFIG_PATH) -> 'SetoConfig | None':
    """Load the configuration file, or return ``None`` when it does not exist."""
    if not os.path.exists(path):
      return None

    try:
      with open(path, encoding='utf-8') as file:
        data = json.load(file)
    except Exception as exception:
      message = f"Unable to read configuration file '{path}': {exception}"
      raise SetoConfigError(message) from exception

    return cls.from_dict(data)

  def save(self, path: str = DEFAULT_CONFIG_PATH) -> None:
    directory = os.path.dirname(path)

    if directory:
      os.makedirs(directory, exist_ok=True)

    self.version = CONFIG_VERSION

    with open(path, 'w', encoding='utf-8') as file:
      json.dump(self.to_dict(), file, indent=2)


class DockerContextManager:
  """Create, inspect and verify Docker contexts on the host running Ṣeto."""

  def __init__(self, *, shell: Shell) -> None:
    self.shell = shell

  def list_contexts(self) -> list[str]:
    try:
      output = self.shell.run(LIST_CONTEXTS_COMMAND, quiet=True, stdout=False)
    except Exception as error:
      raise DockerContextError(
        'Unable to run the Docker CLI on this host. Ensure Docker is installed and '
        f'that the current user may use it: {error}'
      ) from error

    return [line.strip() for line in output.splitlines() if line.strip()]

  def exists(self, name: str) -> bool:
    return name in self.list_contexts()

  def inspect(self, name: str) -> dict[str, Any]:
    output = self.shell.run(f'docker context inspect {shlex.quote(name)}', quiet=True, stdout=False)

    try:
      data = json.loads(output)
    except json.JSONDecodeError as error:
      raise DockerContextError(
        f"Unable to parse the 'docker context inspect {name}' output: {error}"
      ) from error

    if isinstance(data, list) and data and isinstance(data[0], dict):
      return data[0]

    if isinstance(data, dict):
      return data

    raise DockerContextError(f"Unexpected 'docker context inspect {name}' output")

  def endpoint_of(self, name: str) -> str | None:
    data = self.inspect(name)
    endpoints = data.get('Endpoints')

    if not isinstance(endpoints, dict):
      return None

    docker_endpoint_data = endpoints.get('docker')

    if not isinstance(docker_endpoint_data, dict):
      return None

    host = docker_endpoint_data.get('Host')

    return str(host) if host else None

  def create(self, name: str, endpoint: str) -> None:
    self.shell.run(
      f'docker context create {shlex.quote(name)} --docker host={shlex.quote(endpoint)}',
      quiet=True,
      stdout=False,
    )

  def update(self, name: str, endpoint: str) -> None:
    self.shell.run(
      f'docker context update {shlex.quote(name)} --docker host={shlex.quote(endpoint)}',
      quiet=True,
      stdout=False,
    )

  def verify(self, context: str) -> None:
    """Ensure a context can actually reach the Docker daemon it points to."""
    try:
      self.shell.run(f'docker --context {shlex.quote(context)} info', quiet=True, stdout=False)
    except Exception as error:
      raise DockerContextUnavailableError(
        f"Docker context '{context}' cannot reach its Docker daemon: {error}. {DOCKER_CONTEXT_HINT}"
      ) from error

  def ensure(self, node: NodeContext) -> tuple[str, bool]:
    """Create or reuse the Docker context of a node.

    Returns the context name and whether it was created. An existing context
    with a different Docker endpoint is never overwritten: it is reported as a
    conflict so the operator can decide what to do with it.
    """
    context = validate_context_name(node.docker_context or node.derived_context)
    endpoint = node.endpoint

    if not self.exists(context):
      logger.info(
        "Creating Docker context '%s' for node %s (%s)...", context, node.hostname, endpoint
      )
      self.create(context, endpoint)

      return context, True

    current = self.endpoint_of(context)

    if current != endpoint:
      raise DockerContextConflictError(
        f"Docker context '{context}' already exists with the Docker endpoint '{current}' but node "
        f"'{node.hostname}' expects '{endpoint}'. Remove the conflicting context with "
        f"'docker context rm {context}' and re-run 'seto setup'."
      )

    logger.info("Reusing the existing Docker context '%s' for node %s.", context, node.hostname)

    return context, False

  def provision(self, node: NodeContext) -> str:
    """Create (or reuse) and verify the Docker context of a node."""
    context, created = self.ensure(node)

    try:
      self.verify(context)
    except DockerContextUnavailableError:
      if created:
        raise

      logger.warning("Docker context '%s' is stale or unreachable; refreshing it...", context)
      self.update(context, node.endpoint)
      self.verify(context)

    return context


class DockerContextResolver:
  """Canonical node/context to Docker CLI and Docker SDK resolution.

  This is the single place where a configured node is mapped to its Docker
  context, to an explicit ``docker --context`` command line, to an SSH shell
  used for host-level administration, and to a Docker SDK client.
  """

  def __init__(
    self,
    config: SetoConfig | None = None,
    *,
    shell: Shell | None = None,
    manager: DockerContextManager | None = None,
    key_file_path: str | None = None,
    client_factory: Callable[[str | None], DockerClient] | None = None,
  ) -> None:
    self.config = config if config is not None else SetoConfig()
    self.key_file_path = key_file_path
    self._shell = shell
    self._manager = manager
    self._client_factory = client_factory or build_docker_client
    self._clients: dict[str, DockerClient] = {}
    self._manager_fallback_warned = False

  @property
  def manager(self) -> DockerContextManager:
    if self._manager is None:
      self._manager = DockerContextManager(
        shell=self._shell or LocalShell(local_setting(), self.key_file_path),
      )

    return self._manager

  def node_for(self, hostname: str) -> NodeContext | None:
    return self.config.node_for(hostname)

  def context_for(self, hostname: str) -> str | None:
    return self.config.context_for(hostname)

  def require_context(self, hostname: str) -> str:
    context = self.context_for(hostname)

    if not context:
      raise NodeContextMissingError(
        f"Node '{hostname}' has no Docker context registered in {DEFAULT_CONFIG_PATH}. Run "
        f"'seto setup' to create and verify a Docker context for every configured node."
      )

    return validate_context_name(context)

  def manager_context(self) -> str | None:
    node = self.config.storage_node

    return node.docker_context if node is not None else None

  def node_cli(self, hostname: str, subcommand: str) -> str:
    return docker_cli(self.require_context(hostname), subcommand)

  def manager_cli(self, subcommand: str) -> str:
    context = self.manager_context()

    if context is None and not self._manager_fallback_warned:
      self._manager_fallback_warned = True
      logger.warning(
        'No Docker context is registered for the Swarm manager; falling back to the local Docker '
        'daemon ("%s" context). Run "seto setup" to register the manager Docker context.',
        LOCAL_DOCKER_CONTEXT,
      )

    return docker_cli(context, subcommand)

  def local_cli(self, subcommand: str, context: str | None = LOCAL_DOCKER_CONTEXT) -> str:
    return docker_cli(context, subcommand)

  def client(self, context: str | None) -> DockerClient:
    """Build (and cache) the Docker SDK client of a Docker context."""
    key = context or LOCAL_DOCKER_CONTEXT

    if key not in self._clients:
      endpoint = self.endpoint_for(context)

      try:
        self._clients[key] = self._client_factory(endpoint)
      except DockerContextError:
        raise
      except Exception as error:
        raise DockerContextUnavailableError(
          f"Unable to create a Docker client for context '{key}': {error}"
        ) from error

    return self._clients[key]

  def node_client(self, hostname: str) -> DockerClient:
    return self.client(self.require_context(hostname))

  def manager_client(self) -> DockerClient:
    return self.client(self.manager_context())

  def endpoint_for(self, context: str | None) -> str | None:
    if not context or context == LOCAL_DOCKER_CONTEXT:
      return None

    endpoint = self.manager.endpoint_of(context)

    if not endpoint:
      raise DockerContextUnavailableError(
        f"Docker context '{context}' has no Docker endpoint configured"
      )

    return endpoint

  def node_shell(self, hostname: str) -> Shell:
    """Return the host administration shell (SSH) of a configured node."""
    node = self.node_for(hostname)

    if node is None:
      raise NodeContextMissingError(
        f"Node '{hostname}' is not registered in {DEFAULT_CONFIG_PATH}. Run 'seto setup' to "
        f'register the node and its Docker context.'
      )

    if node.local or node.hostname.lower() == socket.gethostname().lower():
      return LocalShell(node.to_setting(), self.key_file_path)

    return RemoteShell(node.to_setting(), self.key_file_path)

  def close(self) -> None:
    for client in self._clients.values():
      try:
        client.close()
      except Exception as error:  # pylint: disable=broad-except
        logger.debug('Unable to close a Docker client: %s', error)

    self._clients.clear()
