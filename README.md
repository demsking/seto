# Ṣeto

Ṣeto is a **command-line orchestration tool** that automates the setup,
management, and synchronization of shared storage volumes using an **NFS
driver**. It provides a simple workflow for managing **stack-based deployments**
across multiple hosts — from setup to mounting and unmounting volumes.

## Overview

Ṣeto streamlines distributed storage management by automating:

- Remote setup of **manager** and **replica** nodes.
- Creation and synchronization of **shared volumes**.
- Automated **mounting/unmounting** of NFS volumes.
- Resolution of **Docker Compose stacks** before deployment.

This tool is ideal for environments using Docker Swarm, Compose stacks, or
custom orchestrations that depend on synchronized shared storage.

## Supported Operating Systems

Ṣeto has been **tested and validated** on the following Linux distributions:

| Distribution | Versions Tested | Status       |
| ------------ | --------------- | ------------ |
| **Fedora**   | 41, 42, 43, 44  | ✅ Supported |

> **Note:** Other Linux distributions (RHEL, Ubuntu, AlmaLinux) may work but are
> not officially tested. All remote nodes must have **SSH**, **Docker**, and
> **NFS client utilities** installed.

## Features

- **Compose Command** – Resolves and validates Docker Compose files.
- **Setup Command** – Configures manager and replica nodes.
- **Create Volumes Command** – Creates and synchronizes NFS volumes.
- **Mount Volumes Command** – Mounts volumes on replicas.
- **Unmount Volumes Command** – Safely detaches shared volumes.
- **Check Command** – Verifies and lists nodes matching placement constraints.

## Global Options

These options apply to all subcommands:

| Option     | Description                        | Example                         |
| ---------- | ---------------------------------- | ------------------------------- |
| `--stack`  | Stack name for grouping resources. | `--stack my-stack`              |
| `--driver` | Driver URI for shared storage.     | `--driver nfs://user:pass@host` |

## Environment Variables

| Variable                         | Description                                                                      | Default                      |
| -------------------------------- | -------------------------------------------------------------------------------- | ---------------------------- |
| `SETO_DEFAULT_VOLUME_MOUNT_MODE` | Default volume mount mode used when mode is omitted in `volumes-nfs` entries.    | `rw`                         |
| `SETO_DEFAULT_NFS_OPTIONS`       | Default `volumes-nfs` mount options when options are omitted in compose entries. | `rw,hard,noatime,nodiratime` |

## Subcommands

### 1. Compose Command

Resolves Docker Compose files before deployment.

```bash
seto --stack <stack-name> --driver <driver-uri> compose
```

**Example**

```bash
seto --stack my-stack --driver nfs://user:pass@host compose
```

### 2. Setup Command

Sets up **manager** and **replica** nodes for NFS synchronization.

```bash
seto --stack <stack-name> --driver <driver-uri> \
  setup --replica <replica-connection-strings>
```

| Option      | Description                                          |
| ----------- | ---------------------------------------------------- |
| `--replica` | Required. Replica connections: `user:pass@hostname`. |

**Example**

```bash
seto --stack my-stack --driver nfs://user:pass@host \
  setup --replica user:pass@replica1 user:pass@replica2
```

### 3. Create Volumes Command

Creates and synchronizes shared NFS volumes across nodes.

```bash
seto --stack <stack-name> --driver <driver-uri> \
  create-volumes --replica <replica-strings> [--force]
```

| Option      | Description                          |
| ----------- | ------------------------------------ |
| `--replica` | Required. Nodes for volume creation. |
| `--force`   | Optional. Forces re-synchronization. |

**Example**

```bash
seto --stack my-stack --driver nfs://user:pass@host \
  create-volumes --replica user:pass@replica1 user:pass@replica2 --force
```

### 4. Mount Volumes Command

Mounts shared NFS volumes on replicas.

```bash
seto --stack <stack-name> --driver <driver-uri> \
  mount-volumes --replica <replica-strings>
```

| Option      | Description                                |
| :---------- | :----------------------------------------- |
| `--replica` | Required. Nodes where volumes are mounted. |

**Example**

```bash
seto --stack my-stack --driver nfs://user:pass@host \
  mount-volumes --replica user:pass@replica1 user:pass@replica2
```

### 5. Unmount Volumes Command

Unmounts shared NFS volumes from replicas.

```bash
seto --stack <stack-name> --driver <driver-uri> \
  unmount-volumes --replica <replica-strings>
```

| Option      | Description                                  |
| :---------- | :------------------------------------------- |
| `--replica` | Required. Nodes where volumes are unmounted. |

**Example**

````bash
seto --stack my-stack --driver nfs://user:pass@host \
  unmount-volumes --replica user:pass@replica1 user:pass@replica2
### 6. Check Command

Verifies and lists nodes matching placement constraints. By default, it resolves constraints from the stack's compose files. Alternatively, dynamic constraints can be passed via the `--constraint` option.

```bash
seto --project <project-name> --manager <manager-uri> \
  check [--constraint <constraint-query>]
````

| Option         | Description                                                    |
| :------------- | :------------------------------------------------------------- |
| `--constraint` | Optional. A specific constraint query to evaluate dynamically. |

**Example (Compose Stack Fallback)**

```bash
seto --project my-project --stack my-stack --manager nfs://user:pass@host check
```

**Example (Dynamic CLI Constraints)**

```bash
seto --project my-project --manager nfs://user:pass@host \
  check --constraint "host.device.gpu == true"
```

## Example Workflow

Typical end-to-end workflow:

```bash
# 1. Setup manager and replicas
seto --stack my-stack --driver nfs://user:pass@host \
  setup --replica user:pass@replica1 user:pass@replica2

# 2. Create and sync volumes
seto --stack my-stack --driver nfs://user:pass@host \
  create-volumes --replica user:pass@replica1 user:pass@replica2 --force

# 3. Mount volumes for usage
seto --stack my-stack --driver nfs://user:pass@host \
  mount-volumes --replica user:pass@replica1 user:pass@replica2

# 4. Deploy stack after mounting
seto --stack my-stack --manager nfs://user@manager-host deploy

# 5. Unmount volumes when finished
seto --stack my-stack --driver nfs://user:pass@host \
  unmount-volumes --replica user:pass@replica1 user:pass@replica2
```

## Error Handling

Ṣeto provides reliable error handling:

- Missing or invalid arguments exit with a **non-zero status**.
- Remote errors are captured and clearly reported.
- Commands are **idempotent** — safe to re-run if interrupted.
- Execution stops immediately on critical errors.

## Notes

- NFS authentication uses the URI:
  `nfs://username:password@hostname`
- Replica connections follow:
  `username:password@hostname`
- `volumes-nfs` entry format is:
  `source:target[:mode[:nfs-options]]`
- `mode` is optional and defaults to `rw`.
- `nfs-options` is optional and defaults to `rw,hard,noatime,nodiratime`.
- Example entries:
  `data:/var/lib/mysql`
  `data:/var/lib/mysql:ro`
  `data:/var/lib/mysql:rw:hard,noatime`.

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

## Docker Swarm Setup

To set up Docker Swarm, you'll first need to ensure you have Docker installed on
your machines. Then, you can initialize Docker Swarm on one of your machines to
act as the manager node, and join other machines as worker nodes. Below are the
general steps to set up Docker Swarm:

1. **Install Docker**

   Make sure Docker is installed on all machines that will participate in the
   Swarm cluster. You can follow the official Docker installation guide for your
   operating system.

2. **Choose Manager Node**

   Select one of your machines to act as the manager node. This machine will be
   responsible for managing the Swarm cluster.

3. **Initialize Swarm**

   SSH into the chosen manager node and run the following command to initialize
   Docker Swarm:

   ```bash
   docker swarm init --advertise-addr <MANAGER_IP>
   ```

   Replace `<MANAGER_IP>` with the IP address of the manager node. This command
   initializes a new Docker Swarm cluster with the manager node.

4. **Join Worker Nodes**

   After initializing the Swarm, Docker will output a command to join other
   nodes to the cluster as worker nodes. Run this command on each machine you
   want to join as a worker node.

   ```bash
   docker swarm join --token <TOKEN> <MANAGER_IP>:<PORT>
   ```

   Replace `<TOKEN>` with the token generated by the `docker swarm init` command
   and `<MANAGER_IP>:<PORT>` with the IP address and port of the manager node.

5. **Verify Swarm Status**

   Once all nodes have joined the Swarm, you can verify the status of the Swarm
   by running the following command on the manager node:

   ```bash
   docker node ls
   ```

   This command will list all nodes in the Swarm along with their status.

## License

Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License.
You may obtain a copy of the License at [LICENSE](https://gitlab.com/demsking/seto/blob/main/LICENSE).
