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
from dataclasses import dataclass
from typing import Any

from .context import DockerContextResolver
from .shell import Shell

logger = logging.getLogger(__name__)

SUPPORTED_DEVICE_CHECKS = {
  'gpu': r'(test -c /dev/nvidiactl || test -c /dev/nvidia0) && (docker info 2>/dev/null | grep -iq nvidia || which nvidia-smi >/dev/null 2>&1) && echo "OK" || (echo "GPU device not found or not configured for Docker" >&2; exit 1)',  # pylint: disable=line-too-long
  'bluetooth': r'(ls -d /sys/class/bluetooth/hci* >/dev/null 2>&1 || hciconfig 2>/dev/null | grep -q "hci") && echo "OK" || (echo "No active Bluetooth controller found" >&2; exit 1)',  # pylint: disable=line-too-long
  'camera': r'ls /dev/video* >/dev/null 2>&1 && echo "OK" || (echo "No video capture device (camera) found" >&2; exit 1)',
  'coral': r'(test -e /dev/apex_0 || lsusb 2>/dev/null | grep -E -iq "Google|Global Unichip") && echo "OK" || (echo "Google Coral Edge TPU not found" >&2; exit 1)',  # pylint: disable=line-too-long
  'zigbee': r'(test -e /dev/ttyUSB0 || test -e /dev/ttyACM0 || test -d /dev/serial) && echo "OK" || (echo "Zigbee device not found" >&2; exit 1)',
  'zwave': r'(test -e /dev/ttyUSB0 || test -e /dev/ttyACM0 || test -d /dev/serial) && echo "OK" || (echo "ZWave device not found" >&2; exit 1)',
}


# pylint: disable=no-else-return,too-many-return-statements,invalid-name,no-else-break,consider-using-in
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


def constraint_groups(placement: Any) -> list[list[str]]:
  """Normalize ``x-placement`` into AND-groups (groups are ORed together)."""
  groups: list[list[str]] = []

  for item in placement or []:
    if isinstance(item, list):
      group = [entry.strip() for entry in item if isinstance(entry, str) and '==' in entry]

      if group:
        groups.append(group)
    elif isinstance(item, str):
      if '==' in item:
        groups.append([item.strip()])

  return groups


@dataclass(frozen=True)
class ComposeTargets:
  """Nodes matching the placement constraints and nodes usable for deployment."""

  matched: list[str]
  deployable: list[str]


class ComposeTargetResolver:
  """Resolve ``x-mode: compose`` placement constraints to deployment targets.

  Host capability matching and Docker daemon accessibility are two distinct
  steps: a node is only a valid Compose deployment target when it matches the
  placement constraints, has a Docker context registered by ``seto setup``, and
  passes the physical device verification. A node that matches the hardware but
  has no usable Docker context is reported and excluded instead of silently
  becoming a target.
  """

  def __init__(
    self,
    *,
    placement: list,
    nodes: list[Any],
    contexts: DockerContextResolver,
    debug: bool = False,
  ) -> None:
    self.placement = placement
    self.nodes = nodes
    self.contexts = contexts
    self.debug = debug

  def resolve(self) -> ComposeTargets:
    if not self.placement:
      raise ValueError('Missing required x-placement field')

    groups = constraint_groups(self.placement)

    if self.debug:
      logger.debug('Placement constraints: %s', self.placement)
      logger.debug('Constraint groups resolved: %s', groups)

    matched = self._match(groups)
    deployable = self._verify(groups, self._context_backed(matched))

    return ComposeTargets(matched=matched, deployable=deployable)

  def _match(self, groups: list[list[str]]) -> list[str]:
    matched_nodes: list[str] = []

    for node in self.nodes:
      if not node.attrs:
        if self.debug:
          logger.debug('Node has no attributes: %s', node)
        continue

      hostname = node.attrs.get('Description', {}).get('Hostname', 'unknown')

      if self.debug:
        logger.debug("Evaluating node '%s'", hostname)
        logger.debug('  Role: %s', node.attrs.get('Spec', {}).get('Role'))
        logger.debug('  Labels: %s', node.attrs.get('Spec', {}).get('Labels'))

      # Check if this node matches at least one group (OR between groups)
      for group in groups:
        match_all = True
        if self.debug:
          logger.debug('  Checking group: %s', group)

        for constraint in group:
          label_key, label_value = constraint.split('==', 1)
          label_key = label_key.strip()
          value = label_value.strip()

          matched = match_node_constraint(node, label_key, value)
          if self.debug:
            logger.debug(
              '    Constraint: %s == %s -> %s', label_key, value, 'MATCHED' if matched else 'FAILED'
            )

          if not matched:
            match_all = False
            break

        if match_all:
          if self.debug:
            logger.debug("  Node '%s' matched group constraints.", hostname)
          if hostname not in matched_nodes:
            matched_nodes.append(hostname)
          break  # Already matched, move to next node
        else:
          if self.debug:
            logger.debug("  Node '%s' failed group constraints.", hostname)

    if self.debug:
      logger.debug('Matched nodes (before physical device checks): %s', matched_nodes)

    if not matched_nodes:
      raise ValueError(f'Unable to found node for placement "{self.placement}"')

    return matched_nodes

  def _context_backed(self, matched_nodes: list[str]) -> list[str]:
    """Keep only the matched nodes that have a Docker context registered."""
    context_nodes: list[str] = []

    for nodename in matched_nodes:
      if self.contexts.context_for(nodename):
        context_nodes.append(nodename)
      else:
        logger.warning(
          "WARNING: Node '%s' matches the placement constraints but has no Docker context "
          "registered; it is not a valid Compose deployment target. Run 'seto setup' to create "
          'and verify its Docker context.',
          nodename,
        )

    if not context_nodes and self.debug:
      logger.debug('No matched node has a Docker context: %s', matched_nodes)

    return context_nodes

  def _verify(self, groups: list[list[str]], context_nodes: list[str]) -> list[str]:
    """Run the physical device verification on every context-backed node."""
    verified_nodes: list[str] = []

    for nodename in context_nodes:
      node_obj = next(
        node for node in self.nodes if node.attrs and node.attrs['Description']['Hostname'] == nodename
      )
      device_checks = self._device_checks(groups, node_obj)
      has_all_devices = True

      if self.debug and device_checks:
        logger.debug("Running physical device verification for node '%s'...", nodename)

      for device_name, expected_val in device_checks:
        if str(expected_val).lower().strip() in ('false', '0'):
          if self.debug:
            logger.debug(
              "  Skipping physical check for '%s' because expected value is '%s'",
              device_name,
              expected_val,
            )
          continue

        device_key = device_name.lower().strip()
        check_cmd = SUPPORTED_DEVICE_CHECKS.get(
          device_key,
          f'test -e /dev/{device_name} && echo "OK" || (echo "{device_name} device not found" >&2; exit 1)',
        )

        if self.debug:
          logger.debug(
            "  Checking device '%s' on '%s' using command: '%s'", device_name, nodename, check_cmd
          )

        try:
          self.run_device_check(nodename, check_cmd)

          if self.debug:
            logger.debug("  Device '%s' verified successfully on '%s'", device_name, nodename)
        except Exception as error:
          if self.debug:
            logger.debug(
              "  Physical verification failed for device '%s' on '%s': %s",
              device_name,
              nodename,
              error,
            )
          logger.warning(
            "WARNING: Node '%s' matched label constraints but physical check failed for device '%s': %s. Excluding node.",
            nodename,
            device_name,
            error,
          )
          has_all_devices = False
          break

      if has_all_devices:
        verified_nodes.append(nodename)

    return verified_nodes

  def run_device_check(self, nodename: str, check_cmd: str) -> None:
    """Run a host-level device check on a node over its administrative SSH shell.

    Device detection inspects the physical host, not the Docker daemon, and
    therefore does not use the node Docker context.
    """
    node_shell: Shell = self.contexts.node_shell(nodename)

    node_shell.connect()

    try:
      node_shell.run(check_cmd, quiet=True, stdout=False, stderr=True)
    finally:
      node_shell.close()

  def _device_checks(self, groups: list[list[str]], node_obj: Any) -> list[tuple[str, str]]:
    """Determine which physical devices must be verified for a matched node."""
    device_checks: list[tuple[str, str]] = []

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

    return device_checks
