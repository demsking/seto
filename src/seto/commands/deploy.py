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
import logging
import re
import subprocess
from typing import Any

from docker import DockerClient

from ..core.command import parse_volumes_args
from ..core.docker import DockerCompose
from ..core.docker import DockerSwarm
from ..core.docker import NODE_HOSTNAME_RE
from ..core.driver import Driver
from ..core.network import resolve_global_networks
from ..core.network import SETO_INTERNAL_NETWORKS
from ..core.parser import resolve_compose_file
from .resolve import resolve

logger = logging.getLogger(__name__)


def seto_provider_command() -> list[str]:
  script = (
    'import json\n'
    'from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n'
    'state={"http":{"routers":{},"services":{}}}\n'
    'class H(BaseHTTPRequestHandler):\n'
    '  def _send(self,code,obj=None):\n'
    '    self.send_response(code)\n'
    '    if obj is None:\n'
    '      self.send_header("Content-Length","0")\n'
    '      self.end_headers()\n'
    '      return\n'
    '    body=json.dumps(obj).encode("utf-8")\n'
    '    self.send_header("Content-Type","application/json")\n'
    '    self.send_header("Content-Length",str(len(body)))\n'
    '    self.end_headers()\n'
    '    self.wfile.write(body)\n'
    '  def log_message(self, fmt, *args):\n'
    '    return\n'
    '  def do_GET(self):\n'
    '    if self.path.rstrip("/")=="/traefik":\n'
    '      self._send(200,state)\n'
    '      return\n'
    '    self._send(404,{"error":"not found"})\n'
    '  def do_PUT(self):\n'
    '    normalized_path=self.path.rstrip("/")\n'
    '    if normalized_path!="/traefik":\n'
    '      self._send(404,{"error":"not found"})\n'
    '      return\n'
    '    length=int(self.headers.get("Content-Length","0"))\n'
    '    raw=self.rfile.read(length) if length else b"{}"\n'
    '    try:\n'
    '      payload=json.loads(raw.decode("utf-8"))\n'
    '      if not isinstance(payload,dict):\n'
    '        raise ValueError("invalid payload")\n'
    '    except Exception:\n'
    '      self._send(400,{"error":"invalid json"})\n'
    '      return\n'
    '    if "http" not in payload and any(k in payload for k in ("routers","services")):\n'
    '      payload={"http":payload}\n'
    '    if "http" in payload and isinstance(payload["http"],dict):\n'
    '      nested_http=payload["http"].get("http")\n'
    '      if isinstance(nested_http,dict):\n'
    '        payload["http"]=nested_http\n'
    '    current = state if isinstance(state, dict) else {}\n'
    '    merged = dict(current)\n'
    '    for key, value in payload.items():\n'
    '      if key == "http" and isinstance(value, dict):\n'
    '        existing = merged.get("http", {})\n'
    '        if not isinstance(existing, dict):\n'
    '          existing = {}\n'
    '        combined = dict(existing)\n'
    '        for nested_key, nested_value in value.items():\n'
    '          if nested_key in {"routers", "services"} and isinstance(nested_value, dict):\n'
    '            section = dict(combined.get(nested_key, {}))\n'
    '            section.update(nested_value)\n'
    '            combined[nested_key] = section\n'
    '          else:\n'
    '            combined[nested_key] = nested_value\n'
    '        merged["http"] = combined\n'
    '      else:\n'
    '        merged[key] = value\n'
    '    state.clear(); state.update(merged)\n'
    '    self._send(204)\n'
    '  def do_DELETE(self):\n'
    '    prefix="/traefik/"\n'
    '    if not self.path.startswith(prefix):\n'
    '      self._send(404,{"error":"not found"})\n'
    '      return\n'
    '    route_id=self.path[len(prefix):].strip("/")\n'
    '    http=state.setdefault("http",{})\n'
    '    routers=http.setdefault("routers",{})\n'
    '    services=http.setdefault("services",{})\n'
    '    routers.pop(route_id,None)\n'
    '    services.pop(route_id,None)\n'
    '    self._send(204)\n'
    'ThreadingHTTPServer(("0.0.0.0",8080),H).serve_forever()\n'
  )
  return ['python', '-u', '-c', script]


def parse_service_vars(entries: dict[str, Any], hostname: str) -> None:
  for key, value in entries.items():
    if isinstance(value, str):
      entries[key] = re.sub(NODE_HOSTNAME_RE, hostname, value)


def get_label_value(labels: dict[str, Any], name: str) -> Any | str:
  for label, value in labels.items():
    if label.endswith(name):
      return value
  return ''


def pick_label_value(labels: dict[str, Any], name: str) -> Any | str:
  for label, value in labels.items():
    if label.endswith(name):
      del labels[label]
      return value
  return ''


def parse_compose_config(
  args,
  driver: Driver,
  manager_client: DockerClient,
  networks_list: list[str],
  swarm_config: dict,
  *,
  compose_config: dict,
  placement: str | list[str],
  composes: list[DockerCompose],
) -> None:
  if compose_config['services']:
    compose = DockerCompose(
      client=manager_client,
      driver=driver,
      config=compose_config,
    )

    compose.prepare_traefik_publication()
    composes.append(compose)


def get_platform_constraints(service: dict) -> list[str]:
  """Return Docker platform strings derived from node.platform.arch placement constraints.

  Scans ``service.deploy.placement.constraints`` for entries of the form
  ``node.platform.arch == <arch>`` and converts each to a Docker platform string
  (e.g. ``linux/amd64``, ``linux/arm64``).

  Returns an empty list when no ``node.platform.arch`` constraints are found,
  which means *all* platforms advertised by the source image should be mirrored.
  """
  constraints = service.get('deploy', {}).get('placement', {}).get('constraints', [])
  platforms = []

  for constraint in constraints:
    if isinstance(constraint, str):
      match = re.match(r'node\.platform\.arch\s*==\s*(\S+)', constraint.strip())

      if match:
        platforms.append(f'linux/{match.group(1)}')

  return platforms


def get_original_image(service_name: str, service: dict) -> str | None:
  """Return the source image of a seto-generated build from its Dockerfile FROM line.

  Services with a ``build`` key use an ``image`` value generated by seto's
  volumes-image logic; the original image only exists as the ``FROM`` line of the
  auto-generated Dockerfile.
  """
  build_ctx = service['build']
  dockerfile_path = build_ctx.get('dockerfile', '') if isinstance(build_ctx, dict) else ''

  if not dockerfile_path:
    return None

  try:
    with open(dockerfile_path, encoding='utf-8') as fh:
      for line in fh:
        stripped = line.strip()

        if stripped.upper().startswith('FROM '):
          return stripped.split(None, 1)[1].strip()
  except OSError as exc:
    logger.warning(
      "Could not read Dockerfile '%s' for service '%s': %s", dockerfile_path, service_name, exc
    )

  return None


def plan_image_mirrors(
  configs: list[dict],
  image_prefix: str,
) -> list[tuple[dict, str, str, list[str]]]:
  """Plan the registry mirror for every service across ``configs``.

  Returns ``(service, original_image, destination_image, platforms)`` tuples in
  first-seen order. Services that cannot be mirrored (no ``image`` entry, no
  ``image_prefix`` for a plain image, or an unreadable generated Dockerfile) are
  skipped.
  """
  mirrors: list[tuple[dict, str, str, list[str]]] = []

  for config in configs:
    for service_name, service in config.get('services', {}).items():
      prefixed_image = service.get('image', '')

      if not prefixed_image:
        continue

      if 'build' in service:
        # The prefixed image name was generated by seto (volumes-image path).
        # Derive the original image from the Dockerfile's FROM line.
        original_image = get_original_image(service_name, service)

        if not original_image:
          logger.warning(
            "Service '%s': cannot determine original image for pull — skipping.", service_name
          )
          continue
      else:
        # Plain image reference: mirror to image_prefix (if set).
        if not image_prefix:
          continue

        original_image = prefixed_image
        service_image_name = prefixed_image.split('/')[-1]  # basename
        prefixed_image = f'{image_prefix}{service_image_name}'

      mirrors.append((service, original_image, prefixed_image, get_platform_constraints(service)))

  return mirrors


def pull_and_push_images(configs: list[dict], image_prefix: str) -> None:
  """Mirror each service image to image_prefix, preserving all advertised platforms.

  Uses ``docker buildx imagetools create`` to copy multi-platform manifests in a
  single operation without requiring a local pull.

  Images shared by several services are copied only once: mirrors are grouped by
  ``(source, destination)`` and their platforms are unioned. An empty platform list
  means *all* advertised platforms, so it always wins over specific ones.

  Platform selection:
  - By default every platform advertised by the source image is mirrored.
  - If the service defines ``deploy.placement.constraints`` entries for
    ``node.platform.arch``, only those platforms are copied.

  For services with a ``build`` key (generated by seto's volumes-image logic) the
  ``image`` field already holds the prefixed destination name; the original image is
  read from the ``FROM`` line of the auto-generated Dockerfile.

  For plain ``image`` services the image is mirrored only when ``image_prefix`` is
  non-empty.

  In both cases the in-memory service dicts are patched after the copies so that the
  subsequent deploy() stage uses the newly pushed prefixed images directly from the
  registry (``service['image']`` is updated and any ``build`` key is removed).
  """
  mirrors = plan_image_mirrors(configs, image_prefix)

  # Group by (source, destination) so an image shared by several services is copied
  # only once. Platforms are unioned; an empty list means "all platforms".
  platforms_by_image: dict[tuple[str, str], list[str]] = {}
  ordered_images: list[tuple[str, str]] = []

  for _service, original_image, destination_image, platforms in mirrors:
    key = (original_image, destination_image)

    if key not in platforms_by_image:
      platforms_by_image[key] = list(platforms)
      ordered_images.append(key)
    elif not platforms:
      platforms_by_image[key] = []
    else:
      merged = platforms_by_image[key]

      if merged:
        for platform in platforms:
          if platform not in merged:
            merged.append(platform)

  for original_image, destination_image in ordered_images:
    platforms = platforms_by_image[(original_image, destination_image)]
    platform_info = ', '.join(platforms) if platforms else 'all platforms'

    logger.info("Mirroring '%s' → '%s' (%s)...", original_image, destination_image, platform_info)

    cmd = ['docker', 'buildx', 'imagetools', 'create', '--tag', destination_image]

    if platforms:
      cmd += ['--platform', ','.join(platforms)]

    cmd.append(original_image)
    subprocess.run(cmd, check=True)

  # Patch the in-memory services so the deploy() stage that follows uses the
  # prefixed image pulled from the registry — no local rebuild required.
  for service, _original_image, destination_image, _platforms in mirrors:
    service['image'] = destination_image
    service.pop('build', None)


def deploy_seto_stack(args, driver: Driver) -> None:
  # Temporary rewrite driver config
  driver.namespace = 'seto'
  driver.stack = None

  logger.info('Configuring seto services...')
  service_alias = 'seto-provider'
  internal_stack = {
    'networks': SETO_INTERNAL_NETWORKS,
    'services': {
      'provider': {
        'image': 'python:3.12-alpine',
        'command': seto_provider_command(),
        'ports': ['8080:8080'],
        'networks': {key: {'aliases': [service_alias]} for key in SETO_INTERNAL_NETWORKS.keys()},
        'deploy': {
          'replicas': 1,
          'placement': {
            'constraints': ['node.role==manager'],
          },
        },
      },
    },
  }

  logger.info('Deploying seto services...')
  swarm = DockerSwarm(
    driver=driver,
    config=internal_stack,
  )

  swarm.info()
  swarm.deploy()
  swarm.ps()
  swarm.logs()

  # Restore initial driver config
  driver.namespace = args.namespace
  driver.stack = args.stack


def deploy_namespace_stack(args, driver: Driver) -> None:
  # Temporary rewrite driver config
  driver.namespace = f'seto-{args.namespace}'
  driver.stack = None

  namespace_networks = resolve_global_networks(
    args.namespace,
    external_networks=[network.get('name', key) for key, network in SETO_INTERNAL_NETWORKS.items()],
  )

  logger.info('Configuring namespace agent...')
  internal_stack = {
    'networks': namespace_networks,
    'services': {
      'agent': {
        'image': 'busybox',
        'command': ['sleep', 'infinity'],
        'networks': list(namespace_networks.keys()),
        'deploy': {
          'mode': 'global',
        },
      },
    },
  }

  # Resolving compose local volumes
  resolved_compose_data, volumes = resolve_compose_file(
    driver=driver,
    image_prefix=args.image_prefix,
    compose_data=internal_stack,
    default_volume_mount_mode=args.default_volume_mount_mode,
    default_nfs_options=args.default_nfs_options,
    inject=True,
  )

  logger.info('Creating namespace volumes...')
  driver.create_volumes(
    volumes=volumes,
    sync=getattr(args, 'sync', True),
    force=getattr(args, 'force', False),
  )

  logger.info('Deploying namespace services...')
  swarm = DockerSwarm(
    driver=driver,
    config=resolved_compose_data,
  )

  swarm.info()
  swarm.deploy()
  swarm.ps()
  swarm.logs()

  # Restore initial driver config
  driver.namespace = args.namespace
  driver.stack = args.stack


def execute_deploy_command(args, driver: Driver) -> None:
  all_volumes = parse_volumes_args(args, driver)

  logger.info('Refreshing seto provider stack...')
  deploy_seto_stack(args, driver)

  if all_volumes:
    logger.info('Automatically creating shared volumes...')

    driver.connect()
    driver.create_volumes(
      volumes=all_volumes,
      sync=getattr(args, 'sync', True),
      force=getattr(args, 'force', False),
    )
    driver.apply_storage_node_changes()
    driver.terminate()

  logger.info('Resolving %s services...', driver.stack_id)
  setattr(args, 'compose', False)
  swarm_config = resolve(args, driver, env_file=getattr(args, 'env_file', None))

  # Swarm node discovery and Swarm orchestration both go through the manager
  # Docker context (or the local daemon when no context is registered).
  manager_client = driver.contexts.manager_client()

  swarm = DockerSwarm(
    client=manager_client,
    driver=driver,
    config=swarm_config,
  )

  setattr(args, 'compose', True)
  networks_list = list(swarm_config['networks'].keys())
  composes_items: list[DockerCompose] = []

  resolve(
    args,
    driver,
    inject=True,
    env_file=getattr(args, 'env_file', None),
    execute=lambda config, placement: parse_compose_config(
      args,
      driver,
      manager_client,
      networks_list,
      swarm_config,
      compose_config=config,
      placement=placement,
      composes=composes_items,
    ),
  )

  use_pull = getattr(args, 'pull', False)

  # Mirror every image in a single pass so an image shared by several services is
  # pushed only once, even when the services are spread across swarm and compose.
  if use_pull:
    pull_configs = ([swarm_config] if swarm_config['services'] else []) + [
      compose.config for compose in composes_items
    ]

    if pull_configs:
      logger.info('Pulling and pushing %s images...', driver.stack_id)
      pull_and_push_images(pull_configs, args.image_prefix)

  if swarm_config['services']:
    if not use_pull:
      swarm.build()

      logger.info('Pushing %s images...', driver.stack_id)
      swarm.push()

    logger.info('Deploying %s swarm environment...', driver.stack_id)

    if logger.level == logging.DEBUG:
      swarm.info()

    swarm.deploy()

    if logger.level == logging.DEBUG:
      swarm.ps()
      swarm.logs()

  if composes_items:
    if not use_pull:
      logger.info('Building %s compose images...', driver.stack_id)

      for compose in composes_items:
        compose.build()
        compose.push()

    logger.info('Pulling %s compose images...', driver.stack_id)

    for compose in composes_items:
      if logger.level == logging.DEBUG:
        compose.info()

      compose.pull()

    logger.info('Deploying %s compose environment...', driver.stack_id)

    for compose in composes_items:
      compose.deploy()

      if logger.level == logging.DEBUG:
        compose.ps()
        compose.logs()
