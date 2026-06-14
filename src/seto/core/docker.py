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
import socket
from typing import Any

from docker import DockerClient  # pylint: disable=import-self

from .driver import Driver
from .parser import resolve_env_vars  # pylint: disable=deprecated-module
from .shell import Setting
from .shell import Shell

logger = logging.getLogger(__name__)


# pylint: disable=no-else-return,too-many-return-statements,invalid-name,no-else-break,consider-using-in
class Docker:
  def __init__(
    self,
    config: dict,
    driver: Driver,
    client: DockerClient,
  ) -> None:
    self.driver = driver
    self.client = client
    self.config = config
    self.localshell = Driver.get_shell(
      Setting.from_connection_string(self.hostname),
    )

  @property
  def shell(self) -> Shell:
    return self.driver.host

  @property
  def resolved_config(self) -> str:
    return resolve_env_vars(json.dumps(self.config))

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

  def build(self) -> None:
    self.localshell.run(
      command=f'docker compose -f - -p {self.driver.stack_id} build --no-cache',
      input=self.resolved_config,
    )

  def push(self) -> None:
    self.localshell.run(
      command=f'docker compose -f - -p {self.driver.stack_id} push',
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


def match_node_constraint(node: Any, key: str, value: str) -> bool:
  if not node.attrs:
    return False

  # Support host.device.<name> (compose internal constraint) and node.labels.<name> (swarm constraint)
  if key.startswith('host.device.'):
    device_name = key[len('host.device.'):]
    labels = node.attrs.get('Spec', {}).get('Labels') or {}
    node_val = labels.get(key)

    if node_val is None:
      node_val = labels.get(key[len('host.'):])  # Try "device.<name>"

    if node_val is None:
      node_val = labels.get(key[len('host.device.'):])  # Try "<name>"

    if node_val is not None:
      return str(node_val).strip() == str(value).strip()

    # If label is not present on the node, return True to allow physical verification to decide
    # only for supported non-audio devices
    if device_name in ('gpu', 'bluetooth', 'camera', 'coral', 'zigbee', 'zwave'):
      return True

    return False

  elif key.startswith('node.labels.'):
    actual_key = key[len('node.labels.'):]
    labels = node.attrs.get('Spec', {}).get('Labels') or {}
    node_val = labels.get(actual_key)

    if node_val is not None:
      return str(node_val).strip() == str(value).strip()

    # If this is a device constraint and label is missing, let physical check decide
    if actual_key == 'device' and value not in ('mic', 'audio'):
      return True

    return False

  elif key in ('host.role', 'node.role'):
    role = node.attrs.get('Spec', {}).get('Role')

    return role is not None and str(role).strip() == str(value).strip()

  elif key in ('host.name', 'host.hostname', 'node.hostname'):
    hostname = node.attrs.get('Description', {}).get('Hostname')

    return hostname is not None and str(hostname).strip() == str(value).strip()

  else:
    # Fallback to direct label matching for backward compatibility
    labels = node.attrs.get('Spec', {}).get('Labels') or {}
    node_val = labels.get(key)

    if node_val is not None:
      return str(node_val).strip() == str(value).strip()

    # Direct fallback for device key
    if key == 'device' and value not in ('mic', 'audio'):
      return True

    return False


class DockerCompose(Docker):
  @property
  def placement(self) -> list:
    value = self.config.get('x-placement', [])

    return value if isinstance(value, list) else [value]

  @property
  @functools.lru_cache(maxsize=128)
  def nodenames(self) -> list[str]:
    if not self.placement:
      raise ValueError('Missing required x-placement field')

    # Normalize to constraint groups (each group is a list of strings to be ANDed)
    groups = []

    for p in self.placement:
      if isinstance(p, list):
        group = [c.strip() for c in p if isinstance(c, str) and '==' in c]

        if group:
          groups.append(group)
      elif isinstance(p, str):
        if '==' in p:
          groups.append([p.strip()])

    if self.driver.debug:
      logger.debug('Placement constraints: %s', self.placement)
      logger.debug('Constraint groups resolved: %s', groups)
      logger.debug('Listing Swarm nodes...')

    nodes = self.client.nodes.list()
    matched_nodes = []

    for node in nodes:
      if not node.attrs:
        if self.driver.debug:
          logger.debug('Node has no attributes: %s', node)
        continue

      hostname = node.attrs.get('Description', {}).get('Hostname', 'unknown')

      if self.driver.debug:
        logger.debug("Evaluating node '%s'", hostname)
        logger.debug('  Role: %s', node.attrs.get('Spec', {}).get('Role'))
        logger.debug('  Labels: %s', node.attrs.get('Spec', {}).get('Labels'))

      # Check if this node matches at least one group (OR between groups)
      for group in groups:
        match_all = True
        if self.driver.debug:
          logger.debug('  Checking group: %s', group)

        for constraint in group:
          label_key, label_value = constraint.split('==', 1)
          label_key = label_key.strip()
          value = label_value.strip()

          matched = match_node_constraint(node, label_key, value)
          if self.driver.debug:
            logger.debug(
              '    Constraint: %s == %s -> %s', label_key, value, 'MATCHED' if matched else 'FAILED'
            )

          if not matched:
            match_all = False
            break

        if match_all:
          if self.driver.debug:
            logger.debug("  Node '%s' matched group constraints.", hostname)
          if hostname not in matched_nodes:
            matched_nodes.append(hostname)
          break  # Already matched, move to next node
        else:
          if self.driver.debug:
            logger.debug("  Node '%s' failed group constraints.", hostname)

    if self.driver.debug:
      logger.debug('Matched nodes (before physical device checks): %s', matched_nodes)

    if not matched_nodes:
      raise ValueError(f'Unable to found node for placement "{self.placement}"')

    supported_device_checks = {
      'gpu': r'(test -c /dev/nvidiactl || test -c /dev/nvidia0) && (docker info 2>/dev/null | grep -iq nvidia || which nvidia-smi >/dev/null 2>&1) && echo "OK" || (echo "GPU device not found or not configured for Docker" >&2; exit 1)',  # pylint: disable=line-too-long
      'bluetooth': r'(ls -d /sys/class/bluetooth/hci* >/dev/null 2>&1 || hciconfig 2>/dev/null | grep -q "hci") && echo "OK" || (echo "No active Bluetooth controller found" >&2; exit 1)',  # pylint: disable=line-too-long
      'camera': r'ls /dev/video* >/dev/null 2>&1 && echo "OK" || (echo "No video capture device (camera) found" >&2; exit 1)',
      'coral': r'(test -e /dev/apex_0 || lsusb 2>/dev/null | grep -E -iq "Google|Global Unichip") && echo "OK" || (echo "Google Coral Edge TPU not found" >&2; exit 1)',  # pylint: disable=line-too-long
      'zigbee': r'(test -e /dev/ttyUSB0 || test -e /dev/ttyACM0 || test -d /dev/serial) && echo "OK" || (echo "Zigbee device not found" >&2; exit 1)',
      'zwave': r'(test -e /dev/ttyUSB0 || test -e /dev/ttyACM0 || test -d /dev/serial) && echo "OK" || (echo "ZWave device not found" >&2; exit 1)',
    }

    verified_nodes = []
    for nodename in matched_nodes:
      # Find the node object to check its constraints
      node_obj = next(
        n for n in nodes if n.attrs and n.attrs['Description']['Hostname'] == nodename
      )

      # Determine if we need to verify devices based on the matching group
      device_checks = []

      for group in groups:
        match_group = True

        for constraint in group:
          label_key, label_value = constraint.split('==', 1)
          label_key = label_key.strip()
          value = label_value.strip()

          if not match_node_constraint(node_obj, label_key, value):
            match_group = False
            break

        if match_group:
          for constraint in group:
            label_key, label_value = constraint.split('==', 1)
            label_key = label_key.strip()
            value = label_value.strip()

            if label_key.startswith('host.device.'):
              device_name = label_key[len('host.device.'):]
              device_checks.append((device_name, value))
            elif label_key == 'node.labels.device' or label_key == 'device':
              device_checks.append((value, 'true'))
          break

      # Run the physical checks on the node
      has_all_devices = True
      if self.driver.debug and device_checks:
        logger.debug("Running physical device verification for node '%s'...", nodename)

      for device_name, expected_val in device_checks:
        if str(expected_val).lower().strip() in ('false', '0'):
          if self.driver.debug:
            logger.debug(
              "  Skipping physical check for '%s' because expected value is '%s'",
              device_name,
              expected_val,
            )
          continue

        device_key = device_name.lower().strip()
        check_cmd = supported_device_checks.get(
          device_key,
          f'test -e /dev/{device_name} && echo "OK" || (echo "{device_name} device not found" >&2; exit 1)',
        )

        if self.driver.debug:
          logger.debug(
            "  Checking device '%s' on '%s' using command: '%s'", device_name, nodename, check_cmd
          )

        try:
          if self.hostname == nodename:
            self.shell.run(check_cmd, quiet=True, stdout=False, stderr=True)
          else:
            setting = Setting.from_connection_string(f'{Driver.setouser}@{nodename}')
            remote_shell = Driver.get_shell(setting, self.shell.key_file_path)
            remote_shell.connect()
            remote_shell.run(check_cmd, quiet=True, stdout=False, stderr=True)
            remote_shell.close()
          if self.driver.debug:
            logger.debug("  Device '%s' verified successfully on '%s'", device_name, nodename)
        except Exception as e:
          if self.driver.debug:
            logger.debug(
              "  Physical verification failed for device '%s' on '%s': %s", device_name, nodename, e
            )
          logger.warning(
            "WARNING: Node '%s' matched label constraints but physical check failed for device '%s': %s. Excluding node.",
            nodename,
            device_name,
            e,
          )
          has_all_devices = False
          break

      if has_all_devices:
        verified_nodes.append(nodename)

    if not verified_nodes:
      raise ValueError(
        f'Unable to found node for placement "{self.placement}" (physical device verification failed for all matched nodes)'
      )

    return verified_nodes

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

  def _remote_node_run(self, command: str, *, hostname: str, input: str) -> None:  # pylint: disable=redefined-builtin
    setting = Setting.from_connection_string(f'{Driver.setouser}@{hostname}')
    node = Driver.get_shell(setting, self.shell.key_file_path)

    node.connect()
    node.run(command, input=input)
    node.close()

  def _exec(self, command: str, nodename: str) -> None:
    if self.hostname == nodename:
      self.shell.run(command=command, input=self.resolved_config)
    else:
      self._remote_node_run(
        command,
        hostname=nodename,
        input=self.resolved_config,
      )

  def pull(self) -> None:
    for nodename in self.nodenames:
      self._exec(f'docker compose -p {self.driver.stack_id} -f - pull', nodename)

  def info(self) -> None:
    if self.driver.manager:
      command = f'docker compose -p {self.driver.stack_id} -f - config'
    else:
      command = f'docker compose -p {self.driver.stack_id} -f - config --no-env-resolution --skip-interpolation'

    for nodename in self.nodenames:
      self._exec(command, nodename)

  def deploy(self) -> None:
    for service in self.config.get('services', {}).values():
      if 'build' in service:
        del service['build']

    for nodename in self.nodenames:
      logger.info('Deploying on %s...', nodename)
      self._exec(
        f'docker compose -p {self.driver.stack_id} -f - up -d --remove-orphans',
        nodename,
      )

  def ps(self) -> None:
    for nodename in self.nodenames:
      logger.info('--- Services on %s ---', nodename)
      self._exec(f'docker compose -p {self.driver.stack_id} -f - ps', nodename)

  def logs(self) -> None:
    for nodename in self.nodenames:
      logger.info('--- Logs on %s ---', nodename)
      self._exec(f'docker compose -p {self.driver.stack_id} -f - logs -n 10', nodename)

  def down(self) -> None:
    for nodename in self.nodenames:
      logger.info('Removing stack from %s...', nodename)
      self._exec(f'docker compose -p {self.driver.stack_id} -f - down', nodename)


class DockerSwarm(Docker):
  @property
  def shell(self) -> Shell:
    return Driver.get_shell(
      Setting.from_connection_string(self.hostname),
    )

  def pull(self) -> None:
    pass

  def info(self) -> None:
    if self.driver.manager:
      command = 'docker stack config -c -'
    else:
      command = 'docker stack config -c - --skip-interpolation'

    self.shell.run(command, input=self.resolved_config)

  def deploy(self) -> None:
    for service in self.config.get('services', {}).values():
      if 'build' in service:
        del service['build']

    self.shell.run(
      command=f'docker stack deploy --prune --detach=true --resolve-image=always -c - {self.driver.stack_id}',
      input=self.resolved_config,
    )

  def ps(self) -> None:
    self.shell.run(f'docker stack ps --no-trunc {self.driver.stack_id}', stderr=False)
    self.shell.run(f'docker stack services {self.driver.stack_id}', stderr=False)

  def logs(self) -> None:
    for service in self.config.get('services', {}).keys():
      self.shell.run(f'docker service logs -n 10 {self.driver.stack_id}_{service}', stderr=False)

  def down(self) -> None:
    self.shell.run(f'docker stack rm {self.driver.stack_id}')
