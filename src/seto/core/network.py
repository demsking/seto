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
import glob
import os
from typing import Any

import yaml


NETWORK_CONFIG = {
  'ipam': {
    'driver': 'default',
  },
}

SETO_NETWORK = {
  **NETWORK_CONFIG,
  'driver': 'overlay',
  'attachable': True,
  'name': 'seto-network',
}

SETO_INTERNAL_NETWORKS = {'network': SETO_NETWORK}


def resolve_networks(
  service_networks: dict[str, Any] | list[str] | None,
  external_networks: list[str] | None = None,
) -> dict[str, Any]:
  networks: dict[str, Any] = {}

  if service_networks:
    if isinstance(service_networks, list):
      for network_key in service_networks:
        networks[network_key] = {}
    else:
      networks = service_networks.copy()

    if external_networks:
      for network_key, network_data in networks.items():
        network_name = network_data.get('name', network_key)

        if network_key in external_networks:
          network_data.clear()

          network_data['external'] = True
        else:
          network_data.update(NETWORK_CONFIG)

        network_data['name'] = network_name

  return networks


def resolve_global_networks(
  namespace: str,
  external_networks: list[str] | None = None,
) -> dict:
  networks_files = glob.glob(os.path.join('networks', '*.yaml'))
  merged_data = {}

  for network_file in networks_files:
    with open(network_file, encoding='utf-8') as file:
      network_key = os.path.splitext(os.path.basename(network_file))[0]
      network_data: dict = yaml.safe_load(file) or {}  # type: ignore
      network_name = network_data.get('name', f'{namespace}_{network_key}')
      merged_data[network_key] = network_data

      if external_networks and network_key in external_networks:
        network_data.clear()

        network_data['external'] = True
      else:
        network_data.update(NETWORK_CONFIG)

      network_data['name'] = network_name

  return merged_data
