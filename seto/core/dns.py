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
import socket


def resolve_hostname(hostname: str) -> str:
  """
  Resolve a hostname to its corresponding IP address.

  Args:
    hostname (str): The hostname to resolve.

  Returns:
    str: The IP address of the hostname, or an error message if the resolution fails.
  """
  return socket.gethostbyname(hostname)
