# Changelog

All notable changes to this project are documented in this file.

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
