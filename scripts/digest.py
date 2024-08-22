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
import argparse
import hashlib


def get_digest(secret: str) -> str:
  """Return the SHA-256 digest of the given secret."""
  sha256_hash = hashlib.sha256()
  sha256_hash.update(secret.encode('utf-8'))
  return sha256_hash.hexdigest()


def main() -> None:
  parser = argparse.ArgumentParser(description='Compute the SHA-256 digest of a secret.')
  parser.add_argument('secret', type=str, help='The secret string to hash.')
  args = parser.parse_args()

  digest = get_digest(args.secret)
  print(f'The digest of "{args.secret}" is: {digest}')


if __name__ == '__main__':
  main()
