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
# pylint: disable=import-error,invalid-name
import os
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from seto.commands.resolve import resolve
from seto.core.driver import Driver
from seto.core.parser import load_env_file
from seto.core.parser import parse_compose_file
from seto.core.parser import parse_local_volumes
from seto.core.parser import parse_nfs_volume_entry
from seto.core.parser import parse_service_configs
from seto.core.parser import parse_services
from seto.core.parser import parse_stack_values
from seto.core.parser import parse_volume_entry
from seto.core.parser import parse_volumes
from seto.core.parser import resolve_compose_file
from seto.core.parser import resolve_env_vars


def test_parse_stack_values():
  entry = {'enable_feature': True, 'debug': False, 'other': 'value', 'number': 123}
  parse_stack_values(entry)
  assert entry['enable_feature'] == 'true'
  assert entry['debug'] == 'false'
  assert entry['other'] == 'value'
  assert entry['number'] == 123


def test_parse_volume_entry():
  assert parse_volume_entry('src:tgt') == ('src', 'tgt', 'rw')
  assert parse_volume_entry('src:tgt:ro') == ('src', 'tgt', 'ro')
  assert parse_volume_entry('src:tgt:ro:extra') == ('src', 'tgt', 'ro')


def test_parse_nfs_volume_entry():
  # Basic
  assert parse_nfs_volume_entry('src:tgt') == ('src', 'tgt', 'rw', 'rw,hard,noatime,nodiratime')
  # Custom mode
  assert parse_nfs_volume_entry('src:tgt:ro') == ('src', 'tgt', 'ro', 'rw,hard,noatime,nodiratime')
  # Custom mode and options
  assert parse_nfs_volume_entry('src:tgt:ro:opt1,opt2') == ('src', 'tgt', 'ro', 'opt1,opt2')
  # Permissive: mode omitted, option list provided
  assert parse_nfs_volume_entry('src:tgt:opt1,opt2') == ('src', 'tgt', 'rw', 'opt1,opt2')

  # Error cases
  with pytest.raises(ValueError, match='Invalid volumes-nfs entry'):
    parse_nfs_volume_entry('invalid_entry')


def test_load_env_file(tmp_path):
  env_file = tmp_path / '.env'
  env_file.write_text(
    'KEY1=value1\n# Comment\nKEY2="value2"\nKEY3=\'value3\'\nINVALID_LINE\n', encoding='utf-8'
  )

  # Ensure env is empty
  os.environ.pop('KEY1', None)
  os.environ.pop('KEY2', None)
  os.environ.pop('KEY3', None)

  load_env_file(str(env_file))

  assert os.environ.get('KEY1') == 'value1'
  assert os.environ.get('KEY2') == 'value2'
  assert os.environ.get('KEY3') == 'value3'


def test_resolve_env_vars(mocker):
  # Mock subprocess.run for envsubst
  mock_run = mocker.patch('subprocess.run')
  mock_run.return_value.stdout = 'hello value1 and $${ESCAPED_VAR}'

  result = resolve_env_vars('hello $VAR1 and $${ESCAPED_VAR}')

  assert result == 'hello value1 and $${ESCAPED_VAR}'
  mock_run.assert_called_once()
  args, kwargs = mock_run.call_args
  assert args[0] == ['envsubst']
  assert '__SETO_ESC_VAR_0__' in kwargs['input']


def test_parse_service_configs(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)

  config_file = Path('config.json')
  config_file.write_text('{"foo": "bar"}', encoding='utf-8')

  service = {'volumes': ['./config.json:/etc/config.json:r', 'normal_volume:/data:rw']}
  configs = {}

  # Test without inject
  parse_service_configs('my_service', service, configs=configs, inject=False)

  assert len(service['volumes']) == 1
  assert service['volumes'][0] == 'normal_volume:/data:rw'
  assert 'configs' in service
  assert len(service['configs']) == 1

  config_entry = service['configs'][0]
  assert config_entry['target'] == '/etc/config.json'
  assert config_entry['mode'] == 400  # parse_permission_mode('r') -> 400
  assert config_entry['source'] in configs
  assert configs[config_entry['source']]['file'] == './config.json'

  # Test with inject
  configs_inject = {}
  service_inject = {'volumes': ['./config.json:/etc/config.json:r']}
  parse_service_configs('my_service', service_inject, configs=configs_inject, inject=True)
  source_name = service_inject['configs'][0]['source']
  assert configs_inject[source_name]['content'] == '{"foo": "bar"}'


def test_parse_local_volumes(tmp_path, mocker, monkeypatch):
  mocker.patch('seto.core.parser.resolve_env_vars', lambda x: x)
  monkeypatch.chdir(tmp_path)

  # Setup test file/directory
  source_dir = Path('src_dir')
  source_dir.mkdir()
  compose_file = Path('docker-compose.yaml')

  service = {'image': 'nginx:alpine', 'volumes-image': ['./src_dir:/usr/share/nginx/html']}

  # Run
  parse_local_volumes(
    'my_stack',
    compose_file_path=str(compose_file),
    service_name='web',
    service=service,
    image_prefix='my_registry/',
  )

  assert 'volumes-image' not in service
  assert service['image'] == 'my_registry/my-stack-web:alpine'
  assert service['build']['context'] == '.'

  dockerfile_path = Path('.seto') / 'images' / 'my-stack-web.dockerfile'
  assert dockerfile_path.exists()

  dockerfile_content = dockerfile_path.read_text()
  assert 'FROM nginx:alpine' in dockerfile_content
  assert 'COPY src_dir /usr/share/nginx/html' in dockerfile_content


def test_parse_volumes_nfs(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)

  # Setup driver mock
  driver = MagicMock(spec=Driver)
  driver.resolve_compose_volume.return_value = {'driver': 'local', 'driver_opts': {'type': 'nfs'}}

  compose_file = Path('docker-compose.yaml')

  # Volumes-nfs with local relative folder
  vol_src = Path('nfs_src')
  vol_src.mkdir()

  service = {'volumes': [], 'volumes-nfs': ['./nfs_src:/data:rw']}

  compose_volumes = {}
  volumes_list = []

  parse_volumes(
    driver=driver,
    service_name='db',
    service=service,
    compose_file_path=str(compose_file),
    compose_volumes=compose_volumes,
    volumes=volumes_list,
  )

  assert len(volumes_list) == 1
  vol = volumes_list[0]
  assert vol.source == str(vol_src.resolve())
  assert vol.target == 'db-nfs_src'
  assert vol.mount_folder == 'db-nfs_src'

  assert 'db_nfs_src' in compose_volumes
  assert service['volumes'] == ['db_nfs_src:/data:rw']


def test_resolve_compose_file(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)

  driver = MagicMock(spec=Driver)
  driver.stack_id = 'test_stack'
  driver.resolve_compose_volume.return_value = {}

  compose_data = {
    'services': {
      'web': {'image': 'nginx:latest', 'environment': {'DEBUG': True}, 'labels': {'foo': 'bar'}}
    }
  }

  updated, _ = resolve_compose_file(
    driver=driver,
    compose_file_path=str(Path('docker-compose.yaml')),
    compose_data=compose_data,
    image_prefix='',
  )

  web_service = updated['services']['web']
  assert web_service['environment']['DEBUG'] == 'true'
  assert web_service['hostname'] == '{{.Node.Hostname}}'
  assert web_service['deploy']['labels']['foo'] == 'bar'
  assert 'labels' not in web_service  # Swarm mode deletes top-level labels


def test_parse_services(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)

  driver = MagicMock(spec=Driver)
  driver.stack_id = 'stack'
  driver.resolve_compose_volume.return_value = {}

  # Create a test service yaml file
  service_yaml = Path('service.yaml')
  service_yaml.write_text(
    """
version: '3.8'
x-mode: compose
services:
  app:
    image: python:rc
""",
    encoding='utf-8',
  )

  mocker.patch('glob.glob', return_value=[str(service_yaml)])
  mocker.patch('seto.core.parser.load_env_file')

  resolved_list, _ = parse_services(
    driver=driver,
    stack=str(tmp_path),
    image_prefix='',
  )

  assert len(resolved_list) == 1
  assert 'app' in resolved_list[0]['services']
  # x-mode compose preserves top-level labels
  assert resolved_list[0]['services']['app']['image'] == 'python:rc'


def test_resolve_uses_x_placement_only_for_compose_mode(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)

  driver = MagicMock(spec=Driver)
  driver.stack_id = 'stack'
  driver.resolve_compose_volume.return_value = {}

  swarm_yaml = tmp_path / 'swarm.yaml'
  swarm_yaml.write_text(
    """
version: '3.8'
x-placement:
  - host.device.gpu == true
services:
  web:
    image: nginx:latest
    deploy:
      placement:
        constraints:
          - node.labels.role == web
""",
    encoding='utf-8',
  )

  compose_yaml = tmp_path / 'compose.yaml'
  compose_yaml.write_text(
    """
version: '3.8'
x-mode: compose
x-placement:
  - host.device.camera == true
services:
  camera:
    image: nginx:latest
""",
    encoding='utf-8',
  )

  mocker.patch('seto.core.parser.load_env_file')
  execute = MagicMock()

  resolved = resolve(
    Namespace(
      namespace='test',
      stack=str(tmp_path),
      image_prefix='',
      default_volume_mount_mode='rw',
      default_nfs_options='rw,hard,noatime,nodiratime',
    ),
    driver,
    execute=execute,
  )

  assert resolved['x-placement'] == ['host.device.camera == true']
  assert set(resolved['services']) == {'web', 'camera'}
  execute.assert_called_once()
  compose_config, placement = execute.call_args.args
  assert compose_config['x-mode'] == 'compose'
  assert placement == ['host.device.camera == true']


def test_resolve_filters_services_by_requested_mode(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)

  driver = MagicMock(spec=Driver)
  driver.stack_id = 'stack'
  driver.resolve_compose_volume.return_value = {}

  swarm_yaml = tmp_path / 'swarm.yaml'
  swarm_yaml.write_text(
    """
services:
  web:
    image: nginx:latest
""",
    encoding='utf-8',
  )

  compose_yaml = tmp_path / 'compose.yaml'
  compose_yaml.write_text(
    """
x-mode: compose
x-placement:
  - host.name == worker
services:
  gpu:
    image: nginx:latest
""",
    encoding='utf-8',
  )

  mocker.patch('seto.core.parser.load_env_file')

  common_args = {
    'namespace': 'test',
    'stack': str(tmp_path),
    'image_prefix': '',
    'default_volume_mount_mode': 'rw',
    'default_nfs_options': 'rw,hard,noatime,nodiratime',
  }

  swarm_only = resolve(Namespace(**common_args, compose=False), driver)
  compose_only = resolve(Namespace(**common_args, compose=True), driver)

  assert set(swarm_only['services']) == {'web'}
  assert swarm_only['x-placement'] is None
  assert set(compose_only['services']) == {'gpu'}
  assert compose_only['x-placement'] == ['host.name == worker']


def test_parse_interpolation(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)
  monkeypatch.setenv('MY_TEST_VAR', 'interpolated_val')

  compose_file = tmp_path / 'docker-compose.yaml'
  compose_file.write_text(
    """
version: '3.8'
services:
  app:
    image: myapp:${MY_TEST_VAR}
""",
    encoding='utf-8',
  )

  mocker.patch('seto.core.parser.load_env_file')

  data, _ = parse_compose_file(str(compose_file))
  assert data['services']['app']['image'] == 'myapp:interpolated_val'

  data_skip, _ = parse_compose_file(str(compose_file), skip_interpolation=True)
  assert data_skip['services']['app']['image'] == 'myapp:${MY_TEST_VAR}'


def test_parse_interpolation_with_custom_env_file(tmp_path, mocker, monkeypatch):
  monkeypatch.chdir(tmp_path)

  (tmp_path / '.env').write_text('MY_TEST_VAR=from_compose\n', encoding='utf-8')
  custom_env = tmp_path / 'custom.env'
  custom_env.write_text('MY_TEST_VAR=from_custom\nEXTRA_VAR=extra\n', encoding='utf-8')

  compose_file = tmp_path / 'docker-compose.yaml'
  compose_file.write_text(
    """
version: '3.8'
services:
  app:
    image: myapp:${MY_TEST_VAR}-${EXTRA_VAR}
""",
    encoding='utf-8',
  )

  data, _ = parse_compose_file(str(compose_file), env_file=str(custom_env))
  assert data['services']['app']['image'] == 'myapp:from_custom-extra'


def test_parse_interpolation_required_var_syntax(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  monkeypatch.setenv('LLDAP_ADMIN_PASSWORD', 'supersecret')

  compose_file = tmp_path / 'docker-compose.yaml'
  compose_file.write_text(
    """
services:
  app:
    environment:
      TINYAUTH_LDAP_BINDPASSWORD: ${LLDAP_ADMIN_PASSWORD?Abort}
""",
    encoding='utf-8',
  )

  data, _ = parse_compose_file(str(compose_file))
  assert data['services']['app']['environment']['TINYAUTH_LDAP_BINDPASSWORD'] == 'supersecret'
