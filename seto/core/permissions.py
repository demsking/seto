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
# Mapping of symbolic permissions to numeric values
_perm_map = {'r': 4, 'w': 2, 'x': 1, '-': 0}


# Helper function to convert each part to numeric value
def _convert_part(part: str) -> int:
  return _perm_map[part[0]] + _perm_map[part[1]] + _perm_map[part[2]]


def parse_permission_mode(symbolic: str) -> int:
  """
  Converts a three-character string representing file permissions to its numeric value.

  Args:
    part (str): A string of length 3, where each character is 'r', 'w', 'x', or '-'.

  Returns:
    int: The numeric value corresponding to the permissions.
  """
  symbolic += '---------'

  # Split the symbolic string into three parts: user, group, others
  user = symbolic[0:3]
  group = symbolic[3:6]
  others = symbolic[6:9]

  # Convert each part to its numeric value
  user_octal = _convert_part(user)
  group_octal = _convert_part(group)
  others_octal = _convert_part(others)

  # Combine the results in octal notation
  octal_mode = f'{user_octal}{group_octal}{others_octal}'

  return int(octal_mode)
