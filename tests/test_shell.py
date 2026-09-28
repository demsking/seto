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
# pylint: disable=invalid-name,import-error,import-outside-toplevel,protected-access
from unittest.mock import MagicMock

import pytest
from seto.core.shell import File
from seto.core.shell import Setting
from seto.core.shell import Shell
from seto.core.volume import Volume


def test_setting_from_connection_string_local(mocker):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='127.0.0.1')

  setting = Setting.from_connection_string('localhost')
  assert setting.hostname == 'localhost'
  assert setting.username is None
  assert setting.password is None
  assert setting.local is True
  assert setting.ip == '127.0.0.1'
  assert str(setting) == 'root@localhost'


def test_setting_from_connection_string_hostname_local(mocker):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='127.0.1.1')
  mocker.patch('socket.gethostname', return_value='my-laptop')

  setting = Setting.from_connection_string('my-laptop')
  assert setting.hostname == 'my-laptop'
  assert setting.local is True
  assert setting.ip == '127.0.1.1'


def test_setting_from_connection_string_remote(mocker):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.5')

  setting = Setting.from_connection_string('admin@my-server')
  assert setting.hostname == 'my-server'
  assert setting.username == 'admin'
  assert setting.password is None
  assert setting.local is False
  assert setting.ip == '192.168.1.5'
  assert str(setting) == 'admin@my-server'


def test_setting_from_connection_string_remote_with_password(mocker):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.5')

  setting = Setting.from_connection_string('admin:secret123@my-server')
  assert setting.hostname == 'my-server'
  assert setting.username == 'admin'
  assert setting.password == 'secret123'
  assert setting.local is False


def test_setting_from_connection_string_invalid():
  with pytest.raises(ValueError, match='Unable to determine username'):
    Setting.from_connection_string('remote-only-host')

  with pytest.raises(ValueError, match='Unable to determine hostname'):
    Setting.from_connection_string('admin@')


def test_setting_equality(mocker):
  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.5')
  s1 = Setting.from_connection_string('admin@host1')
  s2 = Setting.from_connection_string('user2@host1')
  s3 = Setting.from_connection_string('admin@host2')

  assert s1 == s2
  assert s1 != s3


def test_file_operations(mocker):
  mock_shell = MagicMock(spec=Shell)
  mock_shell.run.return_value = 'line1\nline2'

  # Init reads content
  f = File(mock_shell, '/etc/exports')
  mock_shell.run.assert_called_once_with('cat /etc/exports', sudo=True, quiet=True, stdout=False)
  assert f.content == 'line1\nline2'

  # Append existing entry
  mock_shell.run.reset_mock()
  added = f.append('line1')
  assert not added
  mock_shell.run.assert_not_called()

  # Append new entry
  added = f.append('line3')
  assert added
  mock_shell.run.assert_called_once_with('echo "line3" >> /etc/exports', sudo=True, quiet=True)
  assert f.content == 'line1\nline2\nline3'

  # chown
  mock_shell.run.reset_mock()
  f.chown('user1')
  mock_shell.run.assert_called_once_with('chown user1 /etc/exports', sudo=True, quiet=True)

  mock_shell.run.reset_mock()
  f.chown('user1', 'group1')
  mock_shell.run.assert_called_once_with('chown user1:group1 /etc/exports', sudo=True, quiet=True)

  # chmod
  mock_shell.run.reset_mock()
  f.chmod(755)
  mock_shell.run.assert_called_once_with('chmod 755 /etc/exports', sudo=True, quiet=True)

  # chmod letsencrypt override
  f_le = File(mock_shell, '/etc/letsencrypt/live/key.pem')
  mock_shell.run.reset_mock()
  f_le.chmod(755)
  mock_shell.run.assert_called_once_with(
    'chmod 600 /etc/letsencrypt/live/key.pem', sudo=True, quiet=True
  )


def test_nfs_letsencrypt_volume_uses_safe_file_scan(mocker):
  from seto.drivers.nfs import NFSDriver

  mock_shell = MagicMock(spec=Shell)
  mock_shell.run.return_value = ''

  mock_exports = MagicMock()
  mock_exports.append.return_value = True
  mock_exports.content = ''

  driver = NFSDriver(
    stack='gateway',
    namespace='netiwoki',
    host=mock_shell,
    storage_node=mock_shell,
  )

  driver.storage_node.mkdir = MagicMock()
  driver.storage_node.file = MagicMock(return_value=mock_exports)

  driver.add_exports_entry('/mnt/nfs/netiwoki/gateway/traefik-letsencrypt', exports=mock_exports)

  command = driver.storage_node.run.call_args[0][0]
  assert 'ls -A x' not in command
  assert (
    'find /mnt/nfs/netiwoki/gateway/traefik-letsencrypt -type f -exec chmod 600 {} +' in command
  )


def test_nfs_sync_copies_only_relative_sources(mocker):
  from seto.drivers.nfs import NFSDriver

  mock_shell = MagicMock(spec=Shell)
  mock_shell.run.return_value = ''
  mock_shell.nobody_group = 'nogroup'

  mock_exports = MagicMock()
  mock_exports.content = ''

  driver = NFSDriver(
    stack='gateway',
    namespace='netiwoki',
    host=mock_shell,
    storage_node=mock_shell,
  )

  driver.add_exports_entry = MagicMock(side_effect=[False, False])
  driver.storage_node.file = MagicMock(return_value=mock_exports)

  relative_volume = Volume(
    name='app_data',
    source='/tmp/project/data',
    is_relative_source=True,
    target='app-data',
    mount_folder='app-data',
  )
  absolute_volume = Volume(
    name='host_data',
    source='/var/lib/host_data',
    is_relative_source=False,
    target='host-data',
    mount_folder='host-data',
  )

  driver.create_volumes(volumes=[relative_volume, absolute_volume], sync=True, force=False)

  driver.add_exports_entry.assert_any_call(
    '/mnt/nfs/netiwoki/gateway/app-data', exports=mock_exports
  )
  driver.add_exports_entry.assert_any_call(
    '/mnt/nfs/netiwoki/gateway/host-data', exports=mock_exports
  )
  driver.storage_node.copy_volume.assert_called_once_with(
    relative_volume, driver.brick, force=False
  )


def test_local_shell(mocker):
  from seto.shells.local import LocalShell

  mocker.patch('seto.core.shell.resolve_hostname', return_value='127.0.0.1')
  setting = Setting.from_connection_string('localhost')
  shell = LocalShell(setting)

  mock_popen = mocker.patch('subprocess.Popen')
  mock_process = mock_popen.return_value
  mock_process.communicate.return_value = ('stdout_val\n', 'stderr_val\n')
  mock_process.returncode = 0

  # test run
  res = shell.run("echo 'hello'", stdout=False)
  assert res == 'stdout_val'
  mock_popen.assert_called_once()

  # test copy_file
  mock_popen.reset_mock()
  shell.copy_file(local_path='foo', remote_path='bar')
  # copy_file executes "cp foo bar" command
  mock_popen.assert_called()


def test_shell_copy_skips_existing_files_unless_forced(mocker, tmp_path):
  local_file = tmp_path / 'model.bin'
  local_file.write_text('data', encoding='utf-8')
  shell = MagicMock(spec=Shell)
  shell.exists.return_value = True

  Shell._copy(shell, local_path=str(local_file), remote_path='/data/model.bin')
  shell.copy_file.assert_not_called()

  Shell._copy(shell, local_path=str(local_file), remote_path='/data/model.bin', force=True)
  shell.copy_file.assert_called_once_with(
    local_path=str(local_file),
    remote_path='/data/model.bin',
  )


def test_remote_shell(mocker):
  from seto.shells.remote import RemoteShell

  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.100')
  setting = Setting.from_connection_string('admin@my-host')

  # Mock paramiko
  mock_client_class = mocker.patch('paramiko.SSHClient')
  mock_client = mock_client_class.return_value

  shell = RemoteShell(setting)
  shell.connect()

  mock_client.connect.assert_called_once_with(
    hostname='my-host',
    username='admin',
    password=None,
    pkey=None,
  )

  # Mock channel and execution
  mock_stdin = MagicMock()
  mock_stdout = MagicMock()
  mock_stderr = MagicMock()
  mock_stdout.read.return_value = b'remote_out\n'
  mock_stderr.read.return_value = b'remote_err\n'
  mock_stdout.channel.recv_exit_status.return_value = 0

  mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

  res = shell.run('uptime', stdout=False)
  assert res == 'remote_out\n'
  mock_client.exec_command.assert_called_once_with('uptime')


def test_remote_shell_copy_file_uses_sudo_install_path(mocker):
  from seto.shells.remote import RemoteShell

  mocker.patch('seto.core.shell.resolve_hostname', return_value='192.168.1.100')
  mocker.patch('seto.shells.remote.uuid4', return_value='copy-id')
  setting = Setting.from_connection_string('admin@my-host')

  mock_client_class = mocker.patch('paramiko.SSHClient')
  mock_client = mock_client_class.return_value
  mock_sftp = MagicMock()
  mock_client.open_sftp.return_value = mock_sftp

  mock_stdin = MagicMock()
  mock_stdout = MagicMock()
  mock_stderr = MagicMock()
  mock_stdout.read.return_value = b''
  mock_stderr.read.return_value = b''
  mock_stdout.channel.recv_exit_status.return_value = 0
  mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

  shell = RemoteShell(setting)
  shell.connect()
  shell.copy_file(local_path='model.bin', remote_path='/mnt/nfs/app/models/model.bin')

  mock_sftp.put.assert_called_once_with('model.bin', '/tmp/seto-copy-id-model.bin')
  mock_client.exec_command.assert_any_call("sudo sh -c 'mkdir -p /mnt/nfs/app/models'")
  mock_client.exec_command.assert_any_call(
    "sudo sh -c 'cp /tmp/seto-copy-id-model.bin /mnt/nfs/app/models/model.bin'"
  )
  mock_client.exec_command.assert_any_call("sudo sh -c 'rm -f /tmp/seto-copy-id-model.bin'")


def test_provision_docker_access_authorizes_the_administrative_user_key(mocker, tmp_path):
  from seto.drivers.nfs import NFSDriver
  from seto.shells.local import LocalShell

  key_file = tmp_path / 'id_rsa'
  key_file.write_text('PRIVATE KEY', encoding='utf-8')
  (tmp_path / 'id_rsa.pub').write_text('ssh-rsa AAAAB3Nza seto\n', encoding='utf-8')

  setting = Setting(hostname='client1', username='admin', password=None, local=False, ip='10.0.0.5')
  shell = LocalShell(setting, str(key_file))
  recorded: list[str] = []

  def _run(command, **kwargs):
    recorded.append(command)

    if command.startswith('getent passwd'):
      return '/home/admin\n'

    return ''

  mocker.patch.object(shell, 'run', side_effect=_run)
  mkdir = mocker.patch.object(shell, 'mkdir')
  authorized_keys = MagicMock()
  authorized_keys.append.return_value = True
  mocker.patch.object(shell, 'file', return_value=authorized_keys)

  driver = NFSDriver(stack='app', namespace='ns', host=shell, storage_node=shell)
  driver.provision_docker_access(shell, 'admin')

  # The administrative SSH user keeps its own key so the Docker context works
  mkdir.assert_called_once_with('/home/admin/.ssh', user='admin', group='admin', mode='700')
  authorized_keys.append.assert_called_once_with('ssh-rsa AAAAB3Nza seto\n')
  authorized_keys.chown.assert_called_once_with('admin', 'admin')
  authorized_keys.chmod.assert_called_once_with('600')

  # The user can already use Docker: no group change is attempted
  assert not any('usermod' in command for command in recorded)


def test_provision_docker_access_grants_docker_group_only_when_needed(mocker, tmp_path):
  from seto.drivers.nfs import NFSDriver
  from seto.shells.local import LocalShell

  key_file = tmp_path / 'id_rsa'
  key_file.write_text('PRIVATE KEY', encoding='utf-8')
  (tmp_path / 'id_rsa.pub').write_text('ssh-rsa AAAAB3Nza seto\n', encoding='utf-8')

  setting = Setting(hostname='client1', username='admin', password=None, local=False, ip='10.0.0.5')
  shell = LocalShell(setting, str(key_file))
  recorded: list[str] = []

  def _run(command, **kwargs):
    recorded.append(command)

    if command.startswith('getent passwd'):
      return '/home/admin\n'

    if command.startswith('docker info'):
      raise RuntimeError('permission denied while trying to connect to the Docker daemon')

    return ''

  mocker.patch.object(shell, 'run', side_effect=_run)
  mocker.patch.object(shell, 'mkdir')
  authorized_keys = MagicMock()
  authorized_keys.append.return_value = False
  mocker.patch.object(shell, 'file', return_value=authorized_keys)

  driver = NFSDriver(stack='app', namespace='ns', host=shell, storage_node=shell)
  driver.provision_docker_access(shell, 'admin')

  assert 'usermod -aG docker admin' in recorded


def test_provision_docker_access_skips_local_nodes(mocker):
  from seto.drivers.nfs import NFSDriver
  from seto.shells.local import LocalShell

  setting = Setting(hostname='localhost', username=None, password=None, local=True, ip='127.0.0.1')
  shell = LocalShell(setting)
  run = mocker.patch.object(shell, 'run')

  driver = NFSDriver(stack='app', namespace='ns', host=shell, storage_node=shell)
  driver.provision_docker_access(shell, None)

  run.assert_not_called()


def test_provision_docker_access_requires_an_ssh_username(mocker):
  from seto.core.context import DockerContextError
  from seto.drivers.nfs import NFSDriver

  setting = Setting(hostname='client1', username=None, password=None, local=False, ip='10.0.0.5')
  shell = MagicMock(spec=Shell)
  shell.setting = setting
  shell.hostname = 'client1'

  driver = NFSDriver(stack='app', namespace='ns', host=shell, storage_node=shell)

  with pytest.raises(DockerContextError, match='no SSH username'):
    driver.provision_docker_access(shell, None)
