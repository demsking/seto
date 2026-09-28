# Copyright 2026 Sébastien Demanou. All Rights Reserved.
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
# pylint: disable=import-error,protected-access
import json

from seto.core.ports import PortCache


def test_port_cache_returns_none_when_file_is_missing(tmp_path):
  cache = PortCache(str(tmp_path / 'ports.json'))

  assert cache.get(project='stack', service='app', target_port=8080) is None


def test_port_cache_round_trips_reservations(tmp_path):
  path = str(tmp_path / 'ports.json')
  cache = PortCache(path)

  assert cache.reserve(project='stack', service='app', target_port=8080, host_port=32772)
  assert cache.reserve(project='stack', service='web', target_port=80, host_port=32773)
  cache.flush()

  reloaded = PortCache(path)

  assert reloaded.get(project='stack', service='app', target_port=8080) == 32772
  assert reloaded.get(project='stack', service='web', target_port=80) == 32773

  with open(path, encoding='utf-8') as file:
    payload = json.load(file)

  assert payload == {
    'version': 1,
    'ports': {'stack/app/8080': 32772, 'stack/web/80': 32773},
  }


def test_port_cache_reserve_only_reports_real_changes(tmp_path):
  cache = PortCache(str(tmp_path / 'ports.json'))

  assert cache.reserve(project='stack', service='app', target_port=8080, host_port=32772)
  assert not cache.reserve(project='stack', service='app', target_port=8080, host_port=32772)
  assert cache.reserve(project='stack', service='app', target_port=8080, host_port=32774)


def test_port_cache_flush_is_a_noop_without_reservations(tmp_path):
  PortCache(str(tmp_path / 'ports.json')).flush()

  assert not (tmp_path / 'ports.json').exists()


def test_port_cache_ignores_corrupted_and_invalid_entries(tmp_path):
  path = str(tmp_path / 'ports.json')

  with open(path, 'w', encoding='utf-8') as file:
    file.write('{not json')

  cache = PortCache(path)

  assert cache.get(project='stack', service='app', target_port=8080) is None

  # A repaired cache is rewritten from the in-memory state.
  cache.reserve(project='stack', service='app', target_port=8080, host_port=32772)
  cache.flush()

  assert PortCache(path).get(project='stack', service='app', target_port=8080) == 32772


def test_port_cache_drops_non_integer_entries(tmp_path):
  path = str(tmp_path / 'ports.json')

  with open(path, 'w', encoding='utf-8') as file:
    json.dump({'version': 1, 'ports': {'stack/app/8080': 'oops', 'stack/web/80': 32773}}, file)

  cache = PortCache(path)

  assert cache.get(project='stack', service='app', target_port=8080) is None
  assert cache.get(project='stack', service='web', target_port=80) == 32773
