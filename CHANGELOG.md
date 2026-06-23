# Changelog

All notable changes to this project are documented in this file.

## 5.0.0-b4 - 2026-06-23

### Breaking Changes

- Changed `seto deploy` volume synchronization to run by default and replaced the `--sync` opt-in flag with `--no-sync`.
- Limited `x-placement` handling to Compose-mode stacks (`x-mode: compose`); Swarm-mode files now ignore `x-placement` and keep using native Swarm placement constraints.

### Features

- Added `-f` / `--force` to `seto deploy` to overwrite existing volume files during deploy-time synchronization.

### Improvements

- Deploy-time volume synchronization now copies only new files by default, preserving existing remote volume data unless `--force` is provided.
- Kept `seto volumes sync` as an explicit full synchronization command that overwrites existing files.

### Fixes

- Fixed repeated deploy syncs failing with permission errors by uploading remote files to a temporary path and installing them into NFS volume paths with sudo.

### Documentation

- Documented local editable CLI testing with `uv tool install --editable . --force`, similar to an `npm link` workflow.
- Clarified deploy synchronization defaults, `--no-sync`, `--force`, and Compose-only `x-placement` behavior.

### Testing

- Added regression coverage for deploy sync defaults, forced sync overwrites, remote sudo copy behavior, and Compose-only placement resolution.

## 5.0.0-b3 - 2026-06-15

### Features

- Added support for node hostname placeholder interpolation (`{{.Node.Hostname}}` / `{{ .Node.Hostname }}`) in Compose mode during deployment (`seto deploy` under `x-mode: compose`).
- Renamed the `config` command to `resolve` to improve semantic clarity across CLI arguments, internal modules, and documentation.
- Renamed the `--resolve-vars` flag to `--skip-interpolation` on the `resolve` command to align with standard Docker conventions.
- Added `--image-prefix` argument to the `resolve` command, with environment variable support via `SETO_IMAGE_PREFIX`.
- Added support for escaping variables in compose files (using `$${VAR}`) to prevent unintended interpolation.

### Improvements

- Consolidated Docker Compose network definitions during resolution, ensuring a single global networks entry is produced and all services reference it.
- Improved deployment robustness by disabling mandatory checks for logs collection (`swarm.logs()`) when target nodes are offline, and added safer property handling (e.g. `sync` attribute check).
- Made the compose file path optional in `resolve_compose_file` to support dynamic loading.

### Testing

- Implemented a comprehensive project test suite covering CLI parsing, network configuration, shell commands, DNS resolution, and docker compose setup.

## 5.0.0-b2 - 2026-06-14

### Features

- Added explicit image pulling (`docker compose pull`) for Docker Compose deployments before starting services to ensure the latest images are used on target nodes.

### Documentation

- Added detailed installation instructions in README using `pipx` and `uv`.

## 5.0.0-b1 - 2026-06-14

### Breaking Changes

- Grouped volume-related commands (`create`, `sync`, `mount`, `unmount`) under a nested `volumes` subcommand group (e.g., `seto volumes {create,sync,mount,unmount}`).
- Renamed the `--project` CLI flag to `--namespace` to better align with orchestration terminology.
- Renamed the SSH key CLI flag from `--key` to `--ssh-key` and added environment variable support (`SETO_SSH_KEY`).
- Replaced the global `--manager` CLI flag with a persistent, localized configuration file (`.seto/config.json`).
- Refactored storage architecture terminology: replaced "manager/replica" with "storage-node/storage-replicas".
- Simplified the `volumes mount` and `volumes unmount` commands by removing the `--clients` flag so they execute strictly on the local node.
- Removed the `--storage-replicas` flag from the volume creation command.

### Features

- Added multi-host deployment support to Docker Compose mode.
- Added a `check` command to verify placement constraints across nodes.
- Automatically create/provision shared volumes during `deploy` before stack deployment.
- Added support for sharing NFS volumes across multiple services using the `@` prefix.
- Added dynamic detection of the `nobody` group (e.g. `nogroup` vs `nobody`) based on the target Linux distribution.

### Improvements

- Replaced standard `print()` statements with standard Python structured `logging`.
- Automatically resolve relative volume paths against the compose file location.
- Improved command error handling with process exit code verification in the remote shell.
- Overhauled project documentation and README with an architecture diagram, workflows, logo, and table of contents.

## 4.1.0 - 2026-06-14

### Improvements

- Separated `host` and `manager` shells for the NFS driver, allowing local operations to run on the local host and remote operations on the remote manager.
- Fixed NFS driver to work correctly with a local project directory when the manager is remote.
- Added fallback for host operations to localhost settings when the manager is remote.

## 4.0.1 - 2026-06-05

### Fixes

- Replaced `exec()` usage with `run()` for `Shell` compatibility.
- Removed unsupported `split=True` argument from `RemoteShell.run()` calls.

## 4.0.0 - 2026-06-05

### Breaking Changes

- Changed default NFS mount options from `soft,rw` to `rw,hard,noatime,nodiratime`.
- Renamed environment variable `SETO_DEFAULT_NFS_MODE` to `SETO_DEFAULT_VOLUME_MOUNT_MODE`.

### Migration Notes

- If you explicitly used `SETO_DEFAULT_NFS_MODE`, replace it with `SETO_DEFAULT_VOLUME_MOUNT_MODE`.
- If your workloads depended on `soft` mounts, set `SETO_DEFAULT_NFS_OPTIONS` explicitly to preserve previous behavior.

### Improvements

- Clarified and aligned volume mount configuration naming in CLI/config parsing.
- Updated documentation for default mount mode and recommended NFS options.
