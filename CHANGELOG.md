# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Breaking Changes

- `seto setup` now provisions the NFS hosts (SSH key pair generation, NFS server/client packages, SSH authorization) only on the first run, i.e. while `.seto/config.json` is missing. Later runs start from the persisted configuration, only create/verify/repair Docker contexts, skip the `seto` provider stack, and redeploy the namespace agent stack only when the global `networks/*.yaml` definitions changed. The fingerprint of those definitions is persisted as `global_networks_fingerprint` in `.seto/config.json`.

### Improvements

- A Compose HTTP provider route is now removed automatically when it stops being reachable. The `seto-provider` service probes the backend URL of every published route and drops the ones whose target no longer answers, so stopping a target container makes its route disappear from Traefik within 5 seconds instead of leaving a dead route registered. The provider also expires any route that is not renewed within the TTL, which is now 5 seconds (was 30) and configurable with `SETO_TRAEFIK_ROUTE_TTL`.
- `seto deploy` now runs the image build pass only when a Swarm or Compose service declares a `build` entry. Stacks made exclusively of `image` services (and services whose `build` entry was already removed by `--pull`) skip `compose build` and `compose push` instead of running a no-op build of the whole configuration.
- `seto deploy` now reserves the random host port Docker assigns to a Compose Traefik target port in `.seto/ports.json` and reuses it on later deployments, so redeploying a service keeps the same published binding and the Traefik route stays valid. A reserved binding is applied before the first `docker compose up`, which also removes the extra container recreation previously needed to pin a freshly assigned random port.

### Fixes

- The machine running Ṣeto now resolves through Docker's built-in `default` context, so `x-mode: compose` placements such as `host.name == <current-hostname>` work without registering a node or Docker context for it.
- `seto setup` now persists Docker's built-in `default` context for the current node (the machine running Ṣeto) as `"docker_context": "default"` instead of creating a redundant `seto-<hostname>` context for it. The `default` context is verified but never rewritten.
- `seto down` now removes this stack's Compose routes from the `seto-provider` registry under their full `seto-<stack_id>-<route_id>` id. It previously deleted them under the bare router name, so the routes were never actually removed and relied on a TTL to disappear.

## 5.0.0 - 2026-09-25

### Breaking Changes

- Grouped volume-related commands (`create`, `sync`, `mount`, `unmount`) under a nested `volumes` subcommand group (e.g., `seto volumes {create,sync,mount,unmount}`).
- Renamed the `--project` CLI flag to `--namespace` to better align with orchestration terminology.
- Renamed the SSH key CLI flag from `--key` to `--ssh-key` and added environment variable support (`SETO_SSH_KEY`).
- Replaced the global `--manager` CLI flag with a persistent, localized configuration file (`.seto/config.json`).
- Refactored storage architecture terminology: replaced "manager/replica" with "storage-node/storage-replicas".
- Removed the `--clients` flag from `volumes mount` and `volumes unmount`; both commands now execute strictly on the local node.
- Removed the `--storage-replicas` flag from the volume creation command.
- Made the NFS storage base path configurable through `setup --storage-path` and changed its default from `/data` to `/mnt/nfs`.
- Added `nfsvers=4` to the default NFS mount options, so shared volumes now mount over NFSv4 by default.
- Restricted `x-placement` handling to Compose-mode stacks (`x-mode: compose`); Swarm-mode files now ignore `x-placement` and keep using native Swarm placement constraints.
- Deploy-time volume synchronization now runs by default; the `--sync` opt-in flag was replaced with `--no-sync`.
- The top-level `name` field of a Compose file is now honored only in Compose mode and excluded from Swarm/stack output.

### Migration Notes

- Re-run `seto setup` after upgrading. The storage base path is persisted in `.seto/config.json`; pass `--storage-path /data` to keep the previous `/data` location, or migrate existing data to the new `/mnt/nfs` default.
- If a workload depends on a specific NFS version, set `SETO_DEFAULT_NFS_OPTIONS` (or `nfs-options`) explicitly without `nfsvers=4`.
- Replace `seto --manager ...` invocations with the configuration file written by `seto setup`.
- Replace old flags with their new names: `--project` becomes `--namespace`, `--key` becomes `--ssh-key`.
- Replace `volumes mount --clients ...` / `volumes unmount --clients ...` with the flagless local-node commands, and drop `--storage-replicas` from volume creation.

### Features

- Added pull-and-push image mirroring (`seto deploy --pull`) that copies upstream multi-platform image manifests to the `--image-prefix` registry with `docker buildx imagetools create` instead of building locally, supporting registry mirroring, air-gapped deployments, and CI/CD flows.
- Added platform filtering for mirrored images through `node.platform.arch` placement constraints; when no constraint is set, every platform in the source manifest is mirrored.
- Added a Traefik HTTP provider integration for multi-host Docker Compose: `seto` extracts `traefik.http.*` labels, publishes the dynamic route configuration to a central `seto-provider` endpoint with automatic TTL cleanup, and injects the provider and namespace agent stacks during setup/deploy. The endpoint defaults to `http://seto-provider:8080/traefik` and is configurable with `SETO_TRAEFIK_HTTP_ENDPOINT`.
- Added support for the full Docker Compose variable expansion syntax: `${VAR}`, `${VAR:-default}`, `${VAR-default}`, `${VAR:?error}`, `${VAR?error}`, `${VAR:+alternative}`, and `${VAR+alternative}`, including nested references inside default values and single-pass handling of escaped `$${VAR}` expressions.
- Added an `--env-file` option (and `SETO_ENV_FILE` environment variable) to `resolve`, `deploy`, and `down` for loading an additional environment file that overrides variables from the `.env` files next to the compose file.
- Added support for custom Compose project names via the top-level `name` field, used for container and resource naming across all `docker compose` commands.
- Added resolution of local config files declared in the top-level `configs` block: relative `file` sources are resolved against the stack YAML directory and, for `x-mode: compose` deployments, inlined as `content` so remote nodes need no access to the source file.
- Added multi-host deployment support to Docker Compose mode.
- Added a `check` command to verify placement constraints across nodes.
- Automatically create and provision shared volumes during `deploy` before stack deployment.
- Added support for sharing NFS volumes across multiple services using the `@` prefix, including local folders such as `@./data/shared`.
- Added dynamic detection of the `nobody` group (e.g., `nogroup` vs `nobody`) based on the target Linux distribution.

### Improvements

- Deduplicated image mirroring: an image shared by several services is copied once, with the platform filters of those services unioned; an unconstrained service mirrors the full manifest.
- Limited deploy-time volume synchronization to new exports and to volumes with relative `./` or `../` sources, avoiding unnecessary copies of absolute host paths.
- Pruned only top-level volumes that no service references, preserving shared `@` volumes and generated scoped NFS volumes.
- Resolved relative `configs` file sources and relative file bind mounts against the directory containing the stack YAML instead of the current working directory.
- Consolidated internal and declared networks during resolution so a single global networks entry is produced and every service references it.
- Made NFS storage initialization decouple data synchronization from permission handling, so `chown` always runs for new exports, and removed the redundant namespace root export entry.
- Automatically resolve relative volume paths against the compose file location.
- Improved cross-distribution compatibility: user account creation falls back to `adduser` when `useradd` is unavailable, and the `seto` user's home directory is resolved dynamically through `getent passwd` for `.ssh` setup.
- Replaced standard `print()` statements with Python structured `logging`.
- Verified remote command exit codes in the shell layer to improve command error handling.
- Overhauled project documentation and README with an architecture diagram, workflows, logo, and table of contents.
- Widened the supported Python range to `>=3.12` (dropping the `<3.13` upper bound) and replaced the ad-hoc metadata and packaging scripts with a Makefile-driven release workflow based on `uv version`, adding `alpha`/`beta`/`rc`/`patch`/`minor`/`major` bump targets plus `package`, `publish`, and `check-clean` guards.

### Fixes

- Fixed `seto deploy` so stacks declared with `x-mode: compose` are deployed with Docker Compose only and no longer attempt a Docker Swarm stack deployment.
- Fixed repeated deploy syncs failing with permission errors by uploading remote files to a temporary path and installing them into NFS volume paths with sudo.
- Fixed resolution of `name` outside Compose mode and removed it entirely from Swarm-mode output.
- Fixed shared volumes being pruned when a non-shared `volumes-nfs` source reused them, which caused `docker stack deploy` to fail with `undefined volume "<name>"`.
- Fixed detection of relative sources prefixed with `@`, so `@./...` and `@../...` local folders are correctly recognized as syncable.
- Escaped lone dollar signs in the resolved config so Docker CLI commands preserve literal `$` syntax (for example in Traefik labels) instead of interpreting it as variable interpolation.
- Added `nfsvers=4` to the default NFS mount options to pin the NFS version and avoid implicit downgrades.
- Fixed newly created NFS exports not receiving correct permissions when sync was disabled or the volume used an absolute source path.
- Replaced the unsafe `ls`-based Let's Encrypt permission command with a recursive `find` that suppresses errors.
- Handled nested remote directories when copying files through the remote shell.
- Fixed the inline endpoint check to invoke `python3`, restoring compatibility on hosts without a `python` alias.

### Documentation

- Documented Traefik HTTP provider setup, route publication, and the shared overlay network requirement for multi-host Compose deployments.
- Documented pull-and-push mirroring, platform filtering via `node.platform.arch`, and the `docker buildx` requirement.
- Documented local config files (`configs`) resolution and content inlining for Compose deployments.
- Documented custom Compose project names, the `--storage-path` option, custom `--env-file` usage, and the updated NFS defaults and environment variables.
- Documented local editable CLI testing with `uv tool install --editable . --force`, similar to an `npm link` workflow.
- Added installation instructions for `pipx` and `uv`, and added `AGENTS.md` describing the repository's development workflow for coding agents.

### Testing

- Expanded the suite with regression coverage for mode-filtered resolution and compose-only deploy orchestration, deploy sync defaults and forced overwrites, remote sudo copy behavior, and Compose-only placement resolution.
- Added tests for image mirroring (shared images within and across configs, platform merging, the all-platforms override, distinct images, and `FROM` extraction), config file resolution and content injection, variable expansion operators, escaping of dollar signs, and custom Compose project names.
- Added tests for NFS export permissions, safe Let's Encrypt file scanning, shared-volume pruning, and volume sync behavior for relative versus absolute sources.
- Added project test coverage for CLI parsing, network configuration, shell commands, DNS resolution, and Docker Compose setup.

## 5.0.0-b5 - 2026-06-27

### Fixes

- Fixed `seto deploy` so stacks declared with `x-mode: compose` are deployed with Docker Compose only and no longer attempt a Docker Swarm stack deployment.

### Testing

- Added regression coverage for mode-filtered resolution and compose-only deploy orchestration.

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
