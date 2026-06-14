# Ṣeto

<p align="center">
  <img src="assets/logo.png" alt="Ṣeto Logo" width="300" />
</p>

Ṣeto is a robust **command-line orchestration tool** designed to automate the configuration, provisioning, and synchronization of shared storage volumes using an **NFS driver**. It provides a unified, developer-friendly workflow for managing **multi-host stack-based deployments** (supporting both Docker Swarm and multi-host Docker Compose)—taking care of everything from NFS server/client setup and directory synchronization to volume mounting, health verification, and container lifecycle.

## Table of Contents

- [Why Ṣeto?](#why-ṣeto)
- [Overview](#overview)
  - [Key Capabilities](#key-capabilities)
- [Supported Operating Systems](#supported-operating-systems)
- [Features](#features)
- [Usage](#usage)
  - [Configuration & Command Dependencies](#configuration--command-dependencies)
  - [Global Options](#global-options)
  - [Environment Variables](#environment-variables)
- [Subcommands](#subcommands)
  - [1. Setup Command](#1-setup-command)
  - [2. Config Command](#2-config-command)
  - [3. Create Volumes Command](#3-create-volumes-command)
  - [4. Mount Volumes Command](#4-mount-volumes-command)
  - [5. Unmount Volumes Command](#5-unmount-volumes-command)
  - [6. Deploy Command](#6-deploy-command)
  - [7. Down Command](#7-down-command)
  - [8. Check Command](#8-check-command)
- [Example Workflow](#example-workflow)
- [Error Handling](#error-handling)
- [Custom Volume Extensions](#custom-volume-extensions)
  - [1. NFS Shared Volumes (`volumes-nfs`)](#1-nfs-shared-volumes-volumes-nfs)
  - [2. Image-embedded Volumes (`volumes-image`)](#2-image-embedded-volumes-volumes-image)
- [Compose-Specific Deployments (x-mode: compose)](#compose-specific-deployments-x-mode-compose)
  - [Custom Compose Extensions](#custom-compose-extensions)
  - [Targeting Multiple Hosts](#targeting-multiple-hosts)
  - [Physical Device Verification](#physical-device-verification)
  - [Example](#example)
- [Environment Setup](#environment-setup)
- [Makefile Targets](#makefile-targets)
- [License](#license)

## Why Ṣeto?

Deploying containerized applications across multiple servers (using Docker Swarm or Docker Compose) introduces two major challenges that Ṣeto is built to solve:

1. **The Shared Persistent Storage Dilemma**:
   When running a service cluster, containers on different hosts often need to access or modify the same files (e.g., config files, logs, shared uploads, or media directories). Setting up a central NFS server, exporting directories, configuring client-side utilities, mounting paths, updating `/etc/fstab`, and keeping local/remote directories synced is complex, tedious, and prone to configuration drift.

   **How Ṣeto solves it**: It provides a declarative `volumes-nfs` extension. You write simple paths in your Compose file, and Ṣeto automatically installs the NFS server/clients, configures `/etc/exports`, handles dynamic sync, and mounts the volumes cluster-wide.

2. **The Swarm Hardware Limitation**:
   Docker Swarm is excellent for simple stateless microservices but natively struggles with direct hardware mapping. If your containers need to access physical host resources—such as a specific GPU, a USB controller (like Zigbee or Zwave sticks), Bluetooth, cameras, or Edge TPUs—Swarm makes it extremely difficult to map these resources directly.

   **How Ṣeto solves it**: It introduces `x-mode: compose` alongside hardware validation. Ṣeto evaluates custom placement constraints (e.g., `host.device.gpu == true`), physically verifies device drivers on remote nodes over SSH, and deploys services directly via Docker Compose on the target machines—while preserving communication across unified Docker overlay networks.

## Overview

Ṣeto simplifies distributed, multi-host application architecture by orchestrating three key roles:

```mermaid
flowchart TD
    subgraph Swarm Manager / Deploy Host
        CLI[Seto CLI]
        Orchestrator["Swarm Manager (Orchestrator)"]
    end
    subgraph Storage Infrastructure
        SN["Storage Node (NFS Server / Source of Truth)"]
        SR["Storage Replicas (Redundant Sync Targets)"]
    end
    subgraph Application Cluster
        C1["Client Node 1 (Swarm Managed Node)"]
        C2["Client Node 2 (Specific Compose Node)"]
    end

    CLI -- "1. Setup & Provision" --> SN
    CLI -- "2. Sync Volumes" --> SR

    C1 -- "Auto NFS Mount (via Docker)" --> SN
    C2 -- "Auto NFS Mount (via Docker)" --> SN
    SR -- "SSH Sync" --> SN

    CLI -. "mount-volumes (CLI helper)" .-> C1
    CLI -. "mount-volumes (CLI helper)" .-> C2

    CLI -- "deploy (x-mode: swarm)" --> Orchestrator
    Orchestrator -- "Schedules Containers" --> C1
    CLI -- "deploy (x-mode: compose)" --> C2
```

- **Storage Node**: The single source of truth acting as the central NFS Server. It hosts the root folders for all shared volumes.
- **Storage Replicas**: Secondary storage targets configured to sync/replicate volume directories to maintain data availability and redundancy.
- **Client Nodes**: The execution/worker hosts running your application containers. While Docker automatically mounts the NFS volumes inside containers at startup, the CLI mount/unmount commands act as helpers to mount the volume root directly on the client host's filesystem (e.g. for manual inspection or file management).

### Key Capabilities

- **Automated Infrastructure Setup**: Configures SSH, NFS server utilities on the Storage Node, installs client packages, and sets up SSH keys on all nodes.
- **Declarative Storage Management**: Synchronizes local project folders to remote storage locations and handles volume mounting/unmounting lifecycle transparently.
- **Advanced YAML Extensions**: Extends standard Docker Compose files with `volumes-nfs` (for automatic cluster-wide NFS configuration) and `volumes-image` (for baking static assets directly into target images).
- **Flexible Execution Modes**: Deploys services using Docker Swarm (default) or falls back to multi-host Docker Compose (`x-mode: compose`) when direct hardware access (e.g., GPUs, USB devices, cameras) is required.

## Supported Operating Systems

Ṣeto has been **tested and validated** on **Fedora 44, 45**.

Other Linux distributions (RHEL, Ubuntu, AlmaLinux) may work but are not officially tested.
All remote nodes must have **SSH**, **Docker**, and **NFS client utilities** installed.

## Features

- **`config` Command** – Parses, resolves, and renders compose files.
- **`setup` Command** – Configures storage node and storage replica nodes.
- **`create-volumes` Command** – Creates and synchronizes NFS volumes.
- **`mount-volumes` Command** – Mounts volumes on replica nodes.
- **`unmount-volumes` Command** – Safely detaches shared volumes.
- **`deploy` Command** – Deploys or updates Compose/Swarm stacks.
- **`down` Command** – Stops and removes containers, networks, and resources.
- **`check` Command** – Verifies and lists nodes matching placement constraints.

## Usage

Ṣeto provides a command-line interface structured around global options and subcommands.

### Configuration & Command Dependencies

Several subcommands depend on the `.seto/config.json` base configuration file generated by the `setup` command. This establishes a strict operational workflow dependency:

- **NFS Management Commands** (`create-volumes`, `mount-volumes`, `unmount-volumes`) **always** require the `setup` command to have been executed beforehand.
- **Orchestration and Utility Commands** (`config`, `deploy`, `down`) require the `setup` command to have been executed beforehand **only if** the target stack definitions declare NFS (`volumes-nfs`) volumes. If NFS volumes are not used, these commands can be run independently without any prior setup.
- **Node Validation Command** (`check`) evaluates placement constraints without performing volume resolution. It **never** requires the `setup` command to have been executed or the configuration file to exist.

If a dependent command is run before `setup` has generated the configuration file, the command will exit with an error indicating that the setup command has not yet been performed.

### Global Options

These options apply globally to the `seto` CLI tool and must be specified before the subcommand:

| Option             | Required  | Description                                                                     | Example                |
| :----------------- | :-------- | :------------------------------------------------------------------------------ | :--------------------- |
| `--project <name>` | **Yes\*** | Project name for grouping resources (optional for `check` with `--constraint`). | `--project my-project` |
| `--stack <name>`   | No        | Stack name for grouping services and volumes.                                   | `--stack my-stack`     |
| `--key <path>`     | No        | Path to the SSH private key file (defaults to `~/.ssh/id_rsa`).                 | `--key ~/.ssh/id_rsa`  |
| `--debug`          | No        | Enables verbose debug logging.                                                  | `--debug`              |
| `-v, --version`    | No        | Show the version number and exit.                                               | `-v`                   |

### Environment Variables

| Variable                         | Description                                                                      | Default                      |
| -------------------------------- | -------------------------------------------------------------------------------- | ---------------------------- |
| `SETO_DEFAULT_VOLUME_MOUNT_MODE` | Default volume mount mode used when mode is omitted in `volumes-nfs` entries.    | `rw`                         |
| `SETO_DEFAULT_NFS_OPTIONS`       | Default `volumes-nfs` mount options when options are omitted in compose entries. | `rw,hard,noatime,nodiratime` |

## Subcommands

### 1. Setup Command

Sets up the **storage node**, **storage replica**, and **client** nodes for NFS synchronization, and generates the `.seto/config.json` base configuration file.

```bash
seto --project <project-name> [--stack <stack-name>] \
  setup --storage-node <storage-node-uri> --storage-replicas <storage-replica-strings> --clients <client-strings> [--force]
```

| Option               | Description                                                             |
| :------------------- | :---------------------------------------------------------------------- |
| `--storage-node`     | Required. Storage node URI. Format: `nfs://username:password@hostname`. |
| `--storage-replicas` | Required. Storage replica nodes to setup: `user:pass@hostname`.         |
| `--clients`          | Required. Client nodes to setup: `user:pass@hostname`.                  |
| `--force`            | Optional. Forces re-running setup tasks.                                |

**Example:**

```bash
seto --project my-project --stack my-stack \
  setup --storage-node nfs://user:pass@host --storage-replicas user:pass@replica1 user:pass@replica2 --clients user:pass@client1 user:pass@client2
```

---

### 2. Config Command

Parses, resolves environment variables, expands custom volumes, and renders the compose file in canonical format.

```bash
seto --project <project-name> [--stack <stack-name>] \
  config [--compose]
```

| Option      | Description                                                                          |
| :---------- | :----------------------------------------------------------------------------------- |
| `--compose` | Optional. Resolves and compiles for standard Docker Compose instead of Docker Swarm. |

**Example:**

```bash
seto --project my-project --stack my-stack config --compose
```

---

### 3. Create Volumes Command

Creates and synchronizes shared NFS volumes across target storage replica nodes.

```bash
seto --project <project-name> --stack <stack-name> \
  create-volumes --storage-replicas <storage-replica-strings> [--force]
```

| Option               | Description                                                    |
| :------------------- | :------------------------------------------------------------- |
| `--storage-replicas` | Required. Storage replica nodes where volumes will be created. |
| `--force`            | Optional. Forces synchronization of volume data.               |

**Example:**

```bash
seto --project my-project --stack my-stack \
  create-volumes --storage-replicas user:pass@replica1 user:pass@replica2 --force
```

---

### 4. Mount Volumes Command

A helper command that manually mounts the root shared NFS volume directory onto the client hosts' local filesystems (at `/mnt/{brickname}`).

> [!NOTE]
> This command is not required for application containers. Docker automatically mounts the NFS volumes inside the containers when the stack is deployed. This command is a helper for host-level management and manual file access.

```bash
seto --project <project-name> --stack <stack-name> \
  mount-volumes --clients <client-strings>
```

| Option      | Description                                                       |
| :---------- | :---------------------------------------------------------------- |
| `--clients` | Required. Client hosts where the NFS volume root will be mounted. |

**Example:**

```bash
seto --project my-project --stack my-stack \
  mount-volumes --clients user:pass@client1 user:pass@client2
```

---

### 5. Unmount Volumes Command

A helper command that manually unmounts the root shared NFS volume directory from the client hosts' filesystems.

```bash
seto --project <project-name> --stack <stack-name> \
  unmount-volumes --clients <client-strings>
```

| Option      | Description                                                         |
| :---------- | :------------------------------------------------------------------ |
| `--clients` | Required. Client hosts where the NFS volume root will be unmounted. |

**Example:**

```bash
seto --project my-project --stack my-stack \
  unmount-volumes --clients user:pass@client1 user:pass@client2
```

---

### 6. Deploy Command

Deploys a stack to the Swarm cluster or to multiple nodes via Compose (if `x-mode: compose` is set).

> [!IMPORTANT]
> The `deploy` command must be run on the **Swarm Manager** node to perform Swarm service orchestration.

```bash
seto --project <project-name> --stack <stack-name> \
  deploy [--image-prefix <prefix>]
```

| Option           | Description                                                           |
| :--------------- | :-------------------------------------------------------------------- |
| `--image-prefix` | Optional. Prefix added to the image namespace of custom built images. |

**Example:**

```bash
seto --project my-project --stack my-stack deploy
```

---

### 7. Down Command

Stops and removes containers, networks, and stack resources.

```bash
seto --project <project-name> --stack <stack-name> \
  down
```

**Example:**

```bash
seto --project my-project --stack my-stack down
```

---

### 8. Check Command

Verifies and lists nodes matching placement constraints.

```bash
seto [--project <project-name>] check [--constraint <query>]
```

| Option         | Description                                                                                     |
| :------------- | :---------------------------------------------------------------------------------------------- |
| `--constraint` | Optional. A specific placement query to evaluate dynamically (e.g., `host.device.gpu == true`). |

**Example (Compose Stack):**

If the `--constraint` option is not provided, the global `--project` option must be provided to locate the stack's compose files.

```bash
seto --project my-project check
```

**Example (Dynamic CLI Constraints):**

If the `--constraint` option is provided, the global `--project` option is not required.

```bash
seto check --constraint "host.device.gpu == true"
```

## Example Workflow

Typical end-to-end workflow:

```bash
# 1. Setup storage node, storage replicas, and clients (generates .seto/config.json)
seto --project my-project --stack my-stack \
  setup \
    --storage-node nfs://user:pass@host \
    --storage-replicas user:pass@replica1 user:pass@replica2 \
    --clients user:pass@client1 user:pass@client2

# 2. Create and sync volumes
seto --project my-project --stack my-stack \
  create-volumes --storage-replicas user:pass@replica1 user:pass@replica2 --force

# 3. Mount volumes for usage
seto --project my-project --stack my-stack \
  mount-volumes --clients user:pass@client1 user:pass@client2

# 4. Deploy stack after mounting
seto --project my-project --stack my-stack deploy

# 5. Unmount volumes when finished/tearing down
seto --project my-project --stack my-stack \
  unmount-volumes --clients user:pass@client1 user:pass@client2
```

## Error Handling

Ṣeto provides reliable error handling:

- Missing or invalid arguments exit with a **non-zero status**.
- Remote errors are captured and clearly reported.
- Commands are **idempotent** — safe to re-run if interrupted.
- Execution stops immediately on critical errors.

## Custom Volume Extensions

Ṣeto provides two custom volume extensions in Docker Compose/Swarm stack files to simplify shared storage and file deployment: **`volumes-nfs`** and **`volumes-image`**.

These extensions are declared at the service level in your YAML compose configurations. During compilation (e.g., `seto compose` or deployment), Ṣeto parses these keys, translates them into standard Docker/Docker Compose configurations, and handles the necessary backend setups.

---

### 1. NFS Shared Volumes (`volumes-nfs`)

#### **Purpose**

The `volumes-nfs` extension is designed for **shared, dynamic, multi-host persistent storage**. When running services across multiple nodes in Docker Swarm or multi-host Compose, containers on different nodes often need to read and write to the same directory. `volumes-nfs` automates:

- Configuring the storage node as an NFS server.
- Generating proper NFS volume driver configurations in Docker.
- Syncing local file/directory contents from the local development system to the NFS share.
- Mounting/unmounting the NFS share on all replica nodes automatically.

#### **Usage & Format**

```yaml
volumes-nfs:
  - source:target[:mode[:nfs-options]]
```

- **`source`**: Can be a local host directory/file (prefixed with `./` or `~/`) or a named Docker volume. Prefix the source with `@` (e.g., `@shared-data` or `@./data/shared`) to define it as a **shared volume** across multiple services (this prevents Ṣeto from scoping the volume with the service name prefix).
- **`target`**: The destination path inside the container.
- **`mode`** _(optional)_: Mount permissions, either `rw` (read-write) or `ro` (read-only). Defaults to `rw` (or the value of the environment variable `SETO_DEFAULT_VOLUME_MOUNT_MODE`). Legacy option `norename` is also supported for backward compatibility (but `@` source prefix is preferred).
- **`nfs-options`** _(optional)_: Custom NFS mount options. Defaults to `rw,hard,noatime,nodiratime` (or the value of the environment variable `SETO_DEFAULT_NFS_OPTIONS`).

#### **Examples**

```yaml
services:
  web:
    image: nginx:alpine
    volumes-nfs:
      # Mounts the local ./data/static folder to /usr/share/nginx/html on the NFS server with default mode and options
      - ./data/static:/usr/share/nginx/html

      # Mounts a named volume as read-only
      - db-data:/var/lib/mysql:ro

      # Mounts a local directory with custom NFS options
      - ./config:/app/config:rw:rw,soft,noatime

  # Example of sharing the same NFS volume between services
  app:
    image: myapp:latest
    volumes-nfs:
      - @shared-data:/app/data:rw

  worker:
    image: myworker:latest
    volumes-nfs:
      - @shared-data:/app/data:ro
```

#### **How It Works Under the Hood**

1. **Compilation/Translation**: During `seto compose`, Ṣeto strips the `volumes-nfs` block from the service definition, adds a top-level `volumes` definition in the output compose file configured to use the NFS volume driver pointing to the storage node's IP address, and mounts this NFS volume to the service's `volumes` block.
2. **Synchronization**: The `create-volumes` command synchronizes any local source directories (e.g., `./data/static`) to the NFS share directory on the storage node via SSH.
3. **Mounting**: The `mount-volumes` command installs NFS client utilities and mounts the NFS export on the storage replica nodes.

---

### 2. Image-embedded Volumes (`volumes-image`)

#### **Purpose**

The `volumes-image` extension is designed for **static, read-only content** that should be baked directly into the Docker image rather than mounted at runtime. This avoids the latency, security, and setup overhead of mounting an NFS share when a service only needs static files (such as code, configs, or assets that do not change during container execution).

#### **Usage & Format**

```yaml
volumes-image:
  - source:target
```

- **`source`**: The local directory or file on the build/deploy host.
- **`target`**: The destination directory/file inside the container image.

#### **Example**

```yaml
services:
  portal:
    image: nginx:alpine
    volumes-image:
      # Bakes the local folder ./data/static directly into the image at /usr/share/nginx/html
      - ./data/static:/usr/share/nginx/html
```

#### **How It Works Under the Hood**

1. **Dockerfile Generation**: When Ṣeto parses `volumes-image`, it removes it from the service definition and automatically creates a new Dockerfile under the `.seto/images/` directory named `{stack_name}-{service_name}.dockerfile` with the following structure:
   ```dockerfile
   FROM <original_service_image>
   COPY <source> <target>
   ```
2. **Variable Resolution**: Ṣeto runs `envsubst` to resolve any environment variables in the generated Dockerfile.
3. **Compose/Swarm Translation**: It modifies the service's `image` tag to point to a custom image (`{image_prefix}{stack_name}-{service_name}:{image_version}`) and defines a `build` block targeting the generated Dockerfile.
4. **Build and Deployment**: When the stack is built/deployed, the custom image is built with the embedded assets and deployed to the target nodes.

## Compose-Specific Deployments (x-mode: compose)

By default, Ṣeto deploys stacks using Docker Swarm. However, Docker Swarm has
native limitations, such as not supporting direct host device mapping (e.g.,
passing a GPU or a microphone/audio input device to a container).

To work around these Swarm hardware mapping limitations while retaining stack-
based orchestration, Ṣeto supports `x-mode: compose`. In this mode,
deployments are run using `docker compose` directly on target nodes rather
than via the Swarm orchestrator.

Even though compose services run outside the Swarm orchestrator, they still have
access to the same overlay and external networks as Swarm services. Ṣeto
automatically resolves and maps these networks, enabling seamless communication
between Swarm-managed services and Compose-managed services.

### Custom Compose Extensions

Ṣeto parses custom top-level extensions and placement constraints to select
target nodes:

- **`x-mode`** (`string`): Deployment orchestration mode. Must be either
  `swarm` (default) or `compose`.
- **`x-placement`** (`list`): List of target host constraint queries (e.g.
  `host.device.gpu == true`). All Swarm nodes matching these queries will be
  targeted. If `x-placement` is not defined at the top level, Ṣeto automatically
  falls back to service-level constraints defined under
  `deploy.placement.constraints` for all services in the stack.

### Targeting Multiple Hosts

When using `x-mode: compose`, Ṣeto supports targeting **multiple hosts**
Supported constraint keys include:

- `host.device.<name> == <value>`: matches a hardware/device label on the host
  (e.g., `host.device.gpu == true` or `host.device.zigbee == true`).
- `host.role == <role>`: matches the Swarm node role (`manager` or `worker`).
- `host.name == <hostname>`: matches the Swarm node hostname.
- `node.labels.<key> == <value>` (legacy): matches a Swarm node label.
- `node.role == <role>` / `node.hostname == <hostname>` (legacy).

During deployment, lifecycle management, status checks, logging, or removal,
Ṣeto loops over each targeted host to execute the corresponding action.

### Physical Device Verification

When a placement query contains a device constraint (using
`host.device.<name> == <value>` or legacy labels like
`node.labels.device == <name>` / `device == <name>`), Ṣeto connects to the
matching nodes and runs a physical hardware/device check. If the check fails,
the node is excluded from deployment.

Supported devices and their validation commands:

- **`gpu`**: Verifies presence of NVIDIA or Intel/AMD drivers (`/dev/nvidiactl`,
  `/dev/nvidia0`, or `/dev/dri`).
- **`bluetooth`**: Verifies presence of an active Bluetooth controller (via
  `/sys/class/bluetooth/hci*` or `hciconfig`).
- **`camera`**: Verifies presence of a video capture device/webcam (via
  `/dev/video*`).
- **`coral`**: Verifies presence of a Google Coral Edge TPU coprocessor (PCIe or
  USB).
- **`zigbee` / `zwave`**: Verifies presence of serial controllers (`/dev/ttyUSB0`,
  `/dev/ttyACM0`, or `/dev/serial`).
- **Custom devices**: Verifies presence of the device file at `/dev/<name>`.

### Example

```yaml
x-mode: compose
x-placement:
  - host.device.gpu == true
  - host.device.zigbee == true
  - host.role == worker
  - host.name == worker-node-1
```

## Environment Setup

0. See [cloud-init.yaml](cloud-init.yaml) file for prerequisites to install.

1. [Install Devbox](https://www.jetify.com/devbox/docs/installing_devbox/)

2. [Install `direnv` with your OS package manager](https://direnv.net/docs/installation.html#from-system-packages)

3. [Hook it `direnv` into your shell](https://direnv.net/docs/hook.html)

4. **Load environment**

   At the top-level of your project run:

   ```sh
   direnv allow
   ```

   > The next time you will launch your terminal and enter the top-level of your
   > project, `direnv` will check for changes and will automatically load the
   > Devbox environment.

5. **Install dependencies**

   ```sh
   make install
   ```

6. **Start environment**

   ```sh
   make shell
   ```

   This will starts a preconfigured Tmux session.
   Please see the [.tmuxinator.yml](.tmuxinator.yml) file.

## Makefile Targets

Please see the [Makefile](Makefile) for the full list of targets.

## License

Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License.
You may obtain a copy of the License at [LICENSE](https://gitlab.com/demsking/seto/blob/main/LICENSE).
