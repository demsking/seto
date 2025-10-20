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

| Distribution | Versions Tested                | Package Manager | Status       |
|--------------|--------------------------------|-----------------|--------------|
| **Debian**   | 11 (Bullseye), 12 (Bookworm)   | `apt-get`       | ✅ Supported |
| **Fedora**   | 39, 40                         | `dnf`           | ✅ Supported |

> **Note:** Other Linux distributions (RHEL, Ubuntu, AlmaLinux) may work but are
> not officially tested. All remote nodes must have **SSH**, **Docker**, and
> **NFS client utilities** installed.

## Features

- **Compose Command** – Resolves and validates Docker Compose files.
- **Setup Command** – Configures manager and replica nodes.
- **Create Volumes Command** – Creates and synchronizes NFS volumes.
- **Mount Volumes Command** – Mounts volumes on replicas.
- **Unmount Volumes Command** – Safely detaches shared volumes.

## Global Options

These options apply to all subcommands:

| Option       | Description                          | Example                           |
|--------------|--------------------------------------|-----------------------------------|
| `--stack`    | Stack name for grouping resources.   | `--stack my-stack`                |
| `--driver`   | Driver URI for shared storage.       | `--driver nfs://user:pass@host`   |

## Subcommands

### 1. Compose Command

Resolves Docker Compose files before deployment.

```bash
seto --stack <stack-name> --driver <driver-uri> compose
````

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
|-------------|------------------------------------------------------|
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
|-------------|--------------------------------------|
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

```bash
seto --stack my-stack --driver nfs://user:pass@host \
  unmount-volumes --replica user:pass@replica1 user:pass@replica2
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

* Missing or invalid arguments exit with a **non-zero status**.
* Remote errors are captured and clearly reported.
* Commands are **idempotent** — safe to re-run if interrupted.
* Execution stops immediately on critical errors.

## Notes

* NFS authentication uses the URI:
  `nfs://username:password@hostname`
* Replica connections follow:
  `username:password@hostname`
* Future versions will support **GlusterFS**, **CephFS**, and **CIFS**.

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
