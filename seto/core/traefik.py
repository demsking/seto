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
from typing import Any


def deep_set(dct: dict[str, Any], keys: str, value: Any):
  """
  Helper function to set a nested dictionary value using a dot-separated key.
  This handles array notation like 'rewrites[0]' and converts plural-named keys to arrays.

  Args:
    dct: The dictionary to modify.
    keys: A dot-separated string representing the nested keys.
    value: The value to set at the nested location.
  """
  keys_list = keys.replace('[', '.').replace(']', '').split('.')
  current = dct

  for index, key in enumerate(keys_list[:-1]):
    if key.isdigit():  # Convert to list if the key is an index
      key = int(key)
      if not isinstance(current, list):
        current = []

      # Extend the list if the index doesn't exist yet
      while len(current) <= key:
        current.append({})
      current = current[key]
    else:
      if isinstance(current, list):  # Handle case where current is mistakenly a list
        current = current[-1]
      current = current.setdefault(key, {})

  # Handle the last key separately
  final_key = keys_list[-1]

  # Convert plural-named parameters to arrays (e.g. 'middlewares', 'rewrites')
  if final_key.endswith('s') and isinstance(value, str):
    value = [item.strip() for item in value.split(',')]

  if final_key.isdigit():  # Handle if the final key is an array index
    final_key = int(final_key)
    if not isinstance(current, list):
      current = []
    while len(current) <= final_key:
      current.append({})
    current[final_key] = value
  else:
    current[final_key] = value


def convert_middlewares_to_dict(labels: dict[str, str]) -> dict:
  """
  Convert all Traefik middleware labels to JSON format, handling arrays for plural-named keys
  and deeply nested fields.

  Args:
    labels: A dictionary of Docker labels.

  Returns:
    A JSON string representing the middleware configurations.
  """
  middlewares = {}

  for label, value in labels.items():
    if label.startswith('traefik.http.middlewares.'):
      parts = label.split('.')
      if len(parts) > 3:
        middleware_name = parts[3]
        middleware_key = '.'.join(parts[4:])

        if middleware_name not in middlewares:
          middlewares[middleware_name] = {}

        # Use the deep_set helper to handle nested keys
        deep_set(middlewares[middleware_name], middleware_key, value)

  return middlewares
