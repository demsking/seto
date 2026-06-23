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
  shell.copy_file(local_path='model.bin', remote_path='/data/nfs/app/models/model.bin')

  mock_sftp.put.assert_called_once_with('model.bin', '/tmp/seto-copy-id-model.bin')
  mock_client.exec_command.assert_any_call("sudo sh -c 'mkdir -p /data/nfs/app/models'")
  mock_client.exec_command.assert_any_call(
    "sudo sh -c 'cp /tmp/seto-copy-id-model.bin /data/nfs/app/models/model.bin'"
  )
  mock_client.exec_command.assert_any_call("sudo sh -c 'rm -f /tmp/seto-copy-id-model.bin'")
