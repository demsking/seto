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
# pylint: disable=import-error
from seto.core.dns import resolve_hostname


def test_resolve_hostname(mocker):
  mock_gethostbyname = mocker.patch('socket.gethostbyname', return_value='192.168.1.100')

  ip = resolve_hostname('my-test-host')

  assert ip == '192.168.1.100'
  mock_gethostbyname.assert_called_once_with('my-test-host')
