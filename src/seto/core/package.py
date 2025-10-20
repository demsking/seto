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
from importlib.metadata import metadata, PackageNotFoundError

try:
  dist_meta = metadata('seto')
  APP_NAME = dist_meta['Name']
  APP_DESCRIPTION = dist_meta['Summary']
  APP_VERSION = dist_meta['Version']
except PackageNotFoundError:
  APP_NAME = "seto"
  APP_DESCRIPTION = "A Docker Swarm Deployment Manager"
  APP_VERSION = "0.0.0"
