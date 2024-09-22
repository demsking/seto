# Copyright 2024 Sébastien Demanou. All Rights Reserved.
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

import yaml

GLOBAL_NETWORKS = {
  'seto-cloud-public': {
    'name': 'seto-cloud-public',
    'driver': 'overlay',
    'attachable': True,
  },
  'seto-cloud-edge': {
    'name': 'seto-cloud-edge',
    'driver': 'overlay',
    'attachable': True,
  },
  'seto-http-provider': {
    'name': 'seto-http-provider',
    'driver': 'overlay',
    'attachable': True,
  },
}


def get_global_external_networks() -> dict:
  return {
    shortname: {
      'name': network['name'],
      'external': True,
    } for shortname, network in GLOBAL_NETWORKS.items()
  }


def resolve_networks(
  project: str,
  external_networks: list[str] | None = None,
) -> dict:
  networks_files = glob.glob(os.path.join('networks', '*.yaml'))
  merged_data = {}

  for network_file in networks_files:
    with open(network_file, encoding='utf-8') as file:
      network_key = os.path.splitext(os.path.basename(network_file))[0]
      network_data = yaml.safe_load(file)
      network_name = network_data.get('name', f'{project}_{network_key}')
      merged_data[network_key] = network_data

      if external_networks and network_key in external_networks:
        network_data.clear()

        network_data['external'] = True

      network_data['name'] = network_name

  return merged_data
