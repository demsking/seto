# Ṣeto

<p align="center">
  <img src="assets/logo.png" alt="Ṣeto Logo" width="300" />
</p>

Ṣeto is a robust **command-line orchestration tool** designed to automate the configuration, provisioning, and synchronization of shared storage volumes using an **NFS driver**. It provides a unified, developer-friendly workflow for managing **multi-host stack-based deployments** (supporting both Docker Swarm and multi-host Docker Compose)—taking care of everything from NFS server/client setup and directory synchronization to volume mounting, health verification, and container lifecycle.

## Table of Contents

- [Why Ṣeto?](#why-ṣeto)
- [How It Works](#how-it-works)
- [Overview](#overview)
  - [Key Capabilities](#key-capabilities)
- [Supported Operating Systems](#supported-operating-systems)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
  - [Configuration & Command Dependencies](#configuration--command-dependencies)
  - [Global Options](#global-options)
  - [Environment Variables](#environment-variables)
    - [Variable Interpolation](#variable-interpolation)
- [Subcommands](#subcommands)
  - [1. Setup Command](#1-setup-command)
  - [2. Resolve Command](#2-resolve-command)
  - [3. Volumes Command group](#3-volumes-command-group)
    - [3.1. Volumes Create Command](#31-volumes-create-command)
    - [3.2. Volumes Sync Command](#32-volumes-sync-command)
    - [3.3. Volumes Mount Command](#33-volumes-mount-command)
    - [3.4. Volumes Unmount Command](#34-volumes-unmount-command)
  - [4. Deploy Command](#4-deploy-command)
  - [5. Down Command](#5-down-command)
  - [6. Check Command](#6-check-command)
- [Example Workflow](#example-workflow)
- [Error Handling](#error-handling)
- [Custom Volume Extensions](#custom-volume-extensions)
  - [1. NFS Shared Volumes (`volumes-nfs`)](#1-nfs-shared-volumes-volumes-nfs)
  - [2. Image-embedded Volumes (`volumes-image`)](#2-image-embedded-volumes-volumes-image)
- [Compose-Specific Deployments (x-mode: compose)](#compose-specific-deployments-x-mode-compose)
  - [Custom Compose Extensions](#custom-compose-extensions)
  - [Template Variable Replacement](#template-variable-replacement)
  - [Targeting Multiple Hosts](#targeting-multiple-hosts)
  - [Physical Device Verification](#physical-device-verification)
  - [Example](#example)
- [Development](#development)
  - [Environment Setup](#environment-setup)
  - [Local CLI Testing](#local-cli-testing)
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

## How It Works

### How Ṣeto Works

Ṣeto wraps and extends standard Docker orchestration mechanisms. It manages the lifecycle of your application cluster through three main phases:

1. **Infrastructure Provisioning (`setup`)**:
   During the initialization phase, Ṣeto connects to all configured target hosts via SSH. It automates the environment setup by installing `nfs-kernel-server` on the designated Storage Node and `nfs-common` on the Client Nodes. It also establishes SSH key-based authentication across nodes and generates a local configuration file (`.seto/config.json`).

2. **Volume Synchronization (`volumes create`)**:
   For any shared volumes specified via the custom `volumes-nfs` extension in your compose file, Ṣeto:
   - Configures and updates NFS export rules (`/etc/exports`) on the Storage Node.
   - Synchronizes local directory contents (source files) directly to the Storage Node over SSH.

3. **Application Orchestration (`deploy`)**:
   Ṣeto processes deployment configurations based on the specified execution mode:
   - **Docker Swarm Mode (Default)**: Resolves compose files into Swarm-compatible stacks and schedules them on the Swarm manager. The Swarm nodes automatically mount NFS storage volumes inside the starting containers.
   - **Compose Mode (`x-mode: compose`)**: For tasks that require localized hardware integrations (like physical GPUs or USB sticks), Ṣeto verifies constraints and initiates targeted Docker Compose deployments directly on designated client hosts.

### Core Terminologies

To help you get started, here are the main concepts used throughout the Ṣeto ecosystem:

- **Namespace**: A logical grouping and isolation boundary for stacks, storage shares, and networks. All NFS share directories on the Storage Node are organized under the namespace name (e.g. `/data/<namespace>/<stack>`).
- **Stack**: A collection of containerized services, networks, and persistent storage definitions declared in a Compose file that are managed and deployed as a single unit.
- **Storage Node**: The host computer configured to act as the primary, central NFS server. It is the single source of truth for all shared cluster-wide persistent directories.
- **Storage Replicas**: Secondary node locations configured as backup/redundant targets to replicate volumes from the Storage Node for redundancy.
- **Client Nodes**: Target execution hosts (either Swarm worker hosts or independent Docker servers) that run application containers and mount persistent directories.
- **volumes-nfs**: A custom YAML extension key that declares shared cluster-wide directories, auto-configuring backend NFS exports and mounts.
- **volumes-image**: A custom YAML extension key used to bundle and bake static folders or files directly into container images during the build/compilation phase.
- **x-mode**: Specifies whether to run the stack deployment in standard Docker Swarm mode (`swarm`) or localized Docker Compose mode (`compose`).

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
    CLI -- "2. Sync Volumes" --> SN

    C1 -- "Auto NFS Mount (via Docker)" --> SN
    C2 -- "Auto NFS Mount (via Docker)" --> SN
    SN -- "Replication" --> SR

    C1 -. "volumes mount (runs locally)" .-> C1
    C2 -. "volumes mount (runs locally)" .-> C2

    CLI -- "deploy (x-mode: swarm)" --> Orchestrator
    Orchestrator -- "Schedules Containers" --> C1
    CLI -- "deploy (x-mode: compose)" --> C2
```

- **Storage Node**: The single source of truth acting as the central NFS Server. It hosts the root folders for all shared volumes.
- **Storage Replicas**: Secondary storage targets configured to sync/replicate volume directories to maintain data availability and redundancy.
- **Client Nodes**: The execution/worker hosts running your application containers. While Docker automatically mounts the NFS volumes inside containers at startup, the CLI mount/unmount commands can be run directly on any node as helpers to mount the volume root on that node's local filesystem (e.g. for manual inspection or file management).

### Key Capabilities

- **Automated Infrastructure Setup**: Configures SSH, NFS server utilities on the Storage Node, installs client packages, and sets up SSH keys on all nodes.
- **Declarative Storage Management**: Synchronizes local folders to remote storage locations and handles volume mounting/unmounting lifecycle transparently.
- **Advanced YAML Extensions**: Extends standard Docker Compose files with `volumes-nfs` (for automatic cluster-wide NFS configuration) and `volumes-image` (for baking static assets directly into target images).
- **Flexible Execution Modes**: Deploys services using Docker Swarm (default) or falls back to multi-host Docker Compose (`x-mode: compose`) when direct hardware access (e.g., GPUs, USB devices, cameras) is required.

## Supported Operating Systems

Ṣeto has been **tested and validated** on **Fedora 44, 45**.

Other Linux distributions (RHEL, Ubuntu, AlmaLinux) may work but are not officially tested.
All remote nodes must have **SSH**, **Docker**, and **NFS client utilities** installed.

## Features

- **`resolve` Command** – Parses, resolves, and renders compose files.
- **`setup` Command** – Configures storage node and storage replica nodes.
- **`volumes` Command group** – Manage shared volumes (create, sync, mount, unmount).
- **`deploy` Command** – Deploys or updates Compose/Swarm stacks.
- **`down` Command** – Stops and removes containers, networks, and resources.
- **`check` Command** – Verifies and lists nodes matching placement constraints.

## Installation

Ṣeto can be installed from PyPI using standard Python package managers:

### Using `pipx` (Recommended for CLI tools)

To isolate Ṣeto from other system-wide Python packages:

```bash
pipx install seto
```

### Using `uv`

If you are using the modern `uv` Python packaging tool, you can install it as a global tool:

```bash
uv tool install seto
```

## Usage

Ṣeto provides a command-line interface structured around global options and subcommands.

### Configuration & Command Dependencies

Several subcommands depend on the `.seto/config.json` base configuration file generated by the `setup` command. This establishes a strict operational workflow dependency:

- **NFS Management Commands** (`volumes create`, `volumes sync`, `volumes mount`, `volumes unmount`) **always** require the `setup` command to have been executed beforehand.
- **Orchestration and Utility Commands** (`resolve`, `deploy`, `down`) require the `setup` command to have been executed beforehand **only if** the target stack definitions declare NFS (`volumes-nfs`) volumes. If NFS volumes are not used, these commands can be run independently without any prior setup.
- **Node Validation Command** (`check`) evaluates placement constraints without performing volume resolution. It **never** requires the `setup` command to have been executed or the configuration file to exist.

If a dependent command is run before `setup` has generated the configuration file, the command will exit with an error indicating that the setup command has not yet been performed.

### Global Options

These options apply globally to the `seto` CLI tool and must be specified before the subcommand:

| Option               | Required  | Description                                                                  | Example                    |
| :------------------- | :-------- | :--------------------------------------------------------------------------- | :------------------------- |
| `--namespace <name>` | **Yes\*** | Namespace for grouping resources (optional for `check` with `--constraint`). | `--namespace my-namespace` |
| `--stack <name>`     | No        | Stack name for grouping services and volumes.                                | `--stack my-stack`         |
| `--ssh-key <path>`   | No        | Path to the SSH private key file (defaults to `~/.ssh/id_rsa`).              | `--ssh-key ~/.ssh/id_rsa`  |
| `--debug`            | No        | Enables verbose debug logging.                                               | `--debug`                  |
| `-v, --version`      | No        | Show the version number and exit.                                            | `-v`                       |

### Environment Variables

| Variable                         | Description                                                                      | Default                      |
| -------------------------------- | -------------------------------------------------------------------------------- | ---------------------------- |
| `SETO_NAMESPACE`                 | Default namespace name.                                                          | None                         |
| `SETO_STACK`                     | Default stack name.                                                              | None                         |
| `SETO_SSH_KEY`                   | Default path to the SSH private key file.                                        | `~/.ssh/id_rsa`              |
| `SETO_IMAGE_PREFIX`              | Default image namespace prefix to be added to internal images.                   | None                         |
| `SETO_DEFAULT_VOLUME_MOUNT_MODE` | Default volume mount mode used when mode is omitted in `volumes-nfs` entries.    | `rw`                         |
| `SETO_DEFAULT_NFS_OPTIONS`       | Default `volumes-nfs` mount options when options are omitted in compose entries. | `rw,hard,noatime,nodiratime` |

### Variable Interpolation

Ṣeto supports resolving environment variables within your Compose files by default when performing operations (like `resolve` or during stack deployments). You can skip this interpolation by passing the `--skip-interpolation` flag.

#### How it works:

- **Interpolation Syntax**: Use standard syntax like `${VARIABLE}` or `${VARIABLE:-default_value}` in your Compose files.
- **Auto-loaded `.env` Files**: Ṣeto automatically searches for and loads environment variables from a `.env` file and/or a `{stack_name}.env` file (e.g., `my-stack.env`) located in the same directory as the Compose file, prior to performing interpolation.
- **Escaping Interpolation**: If you need to pass a literal `${VARIABLE}` reference to the generated Compose file or container runtime (and prevent Ṣeto from interpolating it), escape it using a double dollar sign: `$${VARIABLE}`. During variable resolution, this will be safely preserved and rendered as `${VARIABLE}` without being replaced by its environment variable value.

## Subcommands

### 1. Setup Command

Sets up the **storage node**, **storage replica**, and **client** nodes for NFS synchronization, and generates the `.seto/config.json` base configuration file.

```bash
seto --namespace <namespace-name> [--stack <stack-name>] \
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
seto --namespace my-namespace --stack my-stack \
  setup --storage-node nfs://user:pass@host --storage-replicas user:pass@replica1 user:pass@replica2 --clients user:pass@client1 user:pass@client2
```

### 2. Resolve Command

Parses, resolves environment variables, expands custom volumes, and renders the compose file in canonical format.

```bash
seto --namespace <namespace-name> [--stack <stack-name>] \
  resolve [--skip-interpolation] [--image-prefix <prefix>]
```

| Option                 | Description                                                                      |
| :--------------------- | :------------------------------------------------------------------------------- |
| `--skip-interpolation` | Optional. Skips interpolation of environment variables inside the compose files. |
| `--image-prefix`       | Optional. Prefix added to the image namespace of custom built images.            |

**Example:**

```bash
seto --namespace my-namespace --stack my-stack resolve --skip-interpolation --image-prefix my-prefix/
```

### 3. Volumes Command Group

Provides subcommands to manage, synchronize, mount, and unmount shared NFS volumes.

#### 3.1. Volumes Create Command

Creates and initializes shared NFS directories and exports on the target storage replica nodes.

```bash
seto --namespace <namespace-name> --stack <stack-name> \
  volumes create
```

**Example:**

```bash
seto --namespace my-namespace --stack my-stack \
  volumes create
```

#### 3.2. Volumes Sync Command

Synchronizes the contents of the local volume source directories directly to the Storage Node.

```bash
seto --namespace <namespace-name> --stack <stack-name> \
  volumes sync
```

**Example:**

```bash
seto --namespace my-namespace --stack my-stack \
  volumes sync
```

#### 3.3. Volumes Mount Command

A helper command that manually mounts the root shared NFS volume directory onto the local filesystem (at `/mnt/{brickname}`) of the node where the command is executed.

> [!NOTE]
> This command is not required for application containers. Docker automatically mounts the NFS volumes inside the containers when the stack is deployed. This command is a helper for host-level management and manual file access.

```bash
seto --namespace <namespace-name> --stack <stack-name> \
  volumes mount
```

**Example:**

```bash
seto --namespace my-namespace --stack my-stack volumes mount
```

#### 3.4. Volumes Unmount Command

A helper command that manually unmounts the root shared NFS volume directory from the local filesystem of the node where the command is executed.

```bash
seto --namespace <namespace-name> --stack <stack-name> \
  volumes unmount
```

**Example:**

```bash
seto --namespace my-namespace --stack my-stack volumes unmount
```

### 4. Deploy Command

Deploys a stack to the Swarm cluster or to multiple nodes via Compose (if `x-mode: compose` is set).

> [!IMPORTANT]
> The `deploy` command must be run on the **Swarm Manager** node to perform Swarm service orchestration.

> [!NOTE]
> If any shared NFS volumes (`volumes-nfs`) are defined in the stack services, the `deploy` command will automatically connect to the storage node, create and synchronize the volumes, and update the exports configuration before the services are started. Deploy-time synchronization copies only new files by default; pass `-f` or `--force` to overwrite existing files.

```bash
seto --namespace <namespace-name> --stack <stack-name> \
  deploy [--image-prefix <prefix>] [--no-sync] [-f|--force]
```

| Option           | Description                                                           |
| :--------------- | :-------------------------------------------------------------------- |
| `--image-prefix` | Optional. Prefix added to the image namespace of custom built images. |
| `--no-sync`      | Optional. Disable shared volume data synchronization before deploy.   |
| `-f, --force`    | Optional. Overwrite existing volume files while syncing.              |

**Example:**

```bash
seto --namespace my-namespace --stack my-stack deploy
```

### 5. Down Command

Stops and removes containers, networks, and stack resources.

```bash
seto --namespace <namespace-name> --stack <stack-name> \
  down
```

**Example:**

```bash
seto --namespace my-namespace --stack my-stack down
```

### 6. Check Command

Verifies and lists nodes matching placement constraints.

```bash
seto [--namespace <namespace-name>] check [--constraint <query>]
```

| Option         | Description                                                                                     |
| :------------- | :---------------------------------------------------------------------------------------------- |
| `--constraint` | Optional. A specific placement query to evaluate dynamically (e.g., `host.device.gpu == true`). |

**Example (Compose Stack):**

If the `--constraint` option is not provided, the global `--namespace` option must be provided to locate the stack's compose files.

```bash
seto --namespace my-namespace check
```

**Example (Dynamic CLI Constraints):**

If the `--constraint` option is provided, the global `--namespace` option is not required.

```bash
seto check --constraint "host.device.gpu == true"
```

## Example Workflow

Typical end-to-end workflow:

```bash
# 1. Setup storage node, storage replicas, and clients (generates .seto/config.json)
seto --namespace my-namespace --stack my-stack \
  setup \
    --storage-node nfs://user:pass@host \
    --storage-replicas user:pass@replica1 user:pass@replica2 \
    --clients user:pass@client1 user:pass@client2

# 2. Deploy stack: will automatically create volumes
seto --namespace my-namespace --stack my-stack deploy
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
2. **Synchronization**: The `volumes create` command synchronizes any local source directories (e.g., `./data/static`) to the NFS share directory on the storage node via SSH.
3. **Mounting**: The `volumes mount` command installs NFS client utilities and mounts the NFS export locally.

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

1. **Dockerfile Generation**: When Ṣeto parses `volumes-image`, it removes it from the service definition and automatically creates a new Dockerfile under the `.seto/images/` directory named `{namespace}-{stack_name}-{service_name}.dockerfile` (with underscores replaced by hyphens) with the following structure:
   ```dockerfile
   FROM <original_service_image>
   COPY <source> <target>
   ```
2. **Variable Resolution**: Ṣeto runs `envsubst` to resolve any environment variables in the generated Dockerfile.
3. **Compose/Swarm Translation**: It modifies the service's `image` tag to point to a custom image (`{image_prefix}{namespace}-{stack_name}-{service_name}:{image_version}`) (with underscores replaced by hyphens) and defines a `build` block targeting the generated Dockerfile.
4. **Build and Deployment**: When the stack is built/deployed, the custom image is built with the embedded assets and deployed to the target nodes.

#### **The Role and Purpose of Image Prefix (`--image-prefix`)**

When using `volumes-image`, Ṣeto dynamically generates a Dockerfile and builds a custom Docker image containing your static files. Because this custom image is built on the deployment/management host, it needs to be pushed to a container registry (or a specific registry namespace) so that all remote nodes in the cluster can pull and run the container.

This is where the **image prefix** plays a critical role:

- **Registry and Namespace Mapping**: It specifies the container registry URL and/or namespace where the custom-built image should be hosted.
- **Image Resolution**: Without a prefix, a service named `web` in a namespace `my-namespace` and stack `my-stack` will resolve to `my-namespace-my-stack-web:alpine`. With `--image-prefix my-registry.com/another-namespace/`, the tag resolves to `my-registry.com/another-namespace/my-namespace-my-stack-web:alpine` (note: any underscores in the namespace or stack name are replaced with hyphens).
- **Consistency**: Both the `resolve` and `deploy` commands support the `--image-prefix` CLI option (and the `SETO_IMAGE_PREFIX` environment variable) to ensure that the generated compose files, local build steps, and target image tags are fully aligned and prefix-resolved correctly before any build or push action occurs.

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

### Template Variable Replacement

In standard Swarm mode, `{{.Node.Hostname}}` is resolved natively by the Swarm engine. However, Docker Compose does not natively support Swarm template placeholders. When running stack deployment in `x-mode: compose`, Ṣeto automatically parses and replaces all occurrences of `{{.Node.Hostname}}` (including variations with spaces like `{{ .Node.Hostname }}`) with the hostname of the current target node during the `info` and `deploy` commands.

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

## Development

### Environment Setup

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

### Local CLI Testing

To test the checked-out project as a globally available `seto` command, use
`uv tool install --editable .` from the repository root. This works like
`npm link`: the command is installed as a tool, while code changes in this
checkout are reflected without rebuilding or reinstalling the package.

```sh
uv tool install --editable . --force
seto --version
```

When you are done testing the local checkout, remove the linked tool:

```sh
uv tool uninstall seto
```

### Makefile Targets

Please see the [Makefile](Makefile) for the full list of targets.

## License

Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License.
You may obtain a copy of the License at [LICENSE](https://gitlab.com/demsking/seto/blob/main/LICENSE).
