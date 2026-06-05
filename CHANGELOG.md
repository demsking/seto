# Changelog

All notable changes to this project are documented in this file.

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
