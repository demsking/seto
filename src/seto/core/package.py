# Copyright 2025 Sébastien Demanou. All Rights Reserved.
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
import tomllib
from pathlib import Path

__all__ = [
  'APP_DESCRIPTION',
  'APP_NAME',
  'APP_VERSION',
]

PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
_pyproject_path = PROJECT_DIRECTORY / 'pyproject.toml'

with _pyproject_path.open('rb') as file:
  package = tomllib.load(file)['project']
  APP_NAME = package['name']
  APP_DESCRIPTION = package['description']
  APP_VERSION = package['version']
  del package
  del tomllib
