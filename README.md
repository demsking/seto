# Ṣeto

Ṣeto is a command-line tool designed to assist with setting up and managing
shared storage volumes using NFS or GlusterFS drivers. It simplifies the process
of configuring stack-based deployments, setting up manager and replica nodes,
creating and syncing shared volumes, and mounting and unmounting these volumes.

### Features

- **Compose Command**: Resolves Docker Compose files.
- **Setup Command**: Sets up manager and replica nodes.
- **Create Volumes Command**: Creates and syncs shared volumes across nodes.
- **Mount Volumes Command**: Mounts shared volumes on specified nodes.
- **Unmount Volumes Command**: Unmounts shared volumes from specified nodes.

### Usage

The main entry point for Ṣeto is the `seto` command. Below is a detailed
description of each subcommand and its options.

#### Global Options

These options are applicable to all subcommands:

- `--stack`: Required. Specifies the stack name.
- `--driver`: Required. Specifies the driver URI to use. Can be `nfs://username:password@hostname` or `gluster://username:password@hostname`.

#### Subcommands

##### 1. Compose Command

Resolves Docker Compose files.

```bash
seto --stack <stack-name> --driver <driver-uri> compose
```

Example:

```bash
seto --stack my-stack --driver nfs://user:pass@host compose
```

##### 2. Setup Command

Sets up the manager and replica nodes.

```bash
seto --stack <stack-name> --driver <driver-uri> setup --replica <replica-connection-strings>
```

- `--replica`: Required. Specifies the nodes to set up in the format `username:password@hostname`.

Example:

```bash
seto --stack my-stack --driver nfs://user:pass@host setup --replica user:pass@replica1 user:pass@replica2
```

##### 3. Create Volumes Command

Creates and syncs shared volumes across nodes.

```bash
seto --stack <stack-name> --driver <driver-uri> create-volumes --replica <replica-connection-strings> [--force]
```

- `--replica`: Required. Specifies the nodes where volumes will be created.
- `--force`: Optional. Forces volume data synchronization.

Example:

```bash
seto --stack my-stack --driver nfs://user:pass@host create-volumes --replica user:pass@replica1 user:pass@replica2 --force
```

##### 4. Mount Volumes Command

Mounts shared volumes on specified nodes.

```bash
seto --stack <stack-name> --driver <driver-uri> mount-volumes --replica <replica-connection-strings>
```

- `--replica`: Required. Specifies the nodes where volumes will be mounted.

Example:

```bash
seto --stack my-stack --driver nfs://user:pass@host mount-volumes --replica user:pass@replica1 user:pass@replica2
```

##### 5. Unmount Volumes Command

Unmounts shared volumes from specified nodes.

```bash
seto --stack <stack-name> --driver <driver-uri> unmount-volumes --replica <replica-connection-strings>
```

- `--replica`: Required. Specifies the nodes where volumes will be unmounted.

Example:

```bash
seto --stack my-stack --driver nfs://user:pass@host unmount-volumes --replica user:pass@replica1 user:pass@replica2
```

### Example Workflow

1. **Setup Manager and Replica Nodes**

```bash
seto --stack my-stack --driver nfs://user:pass@host setup --replica user:pass@replica1 user:pass@replica2
```

2. **Create Volumes**

```bash
seto --stack my-stack --driver nfs://user:pass@host create-volumes --replica user:pass@replica1 user:pass@replica2 --force
```

3. **Mount Volumes**

```bash
seto --stack my-stack --driver nfs://user:pass@host mount-volumes --replica user:pass@replica1 user:pass@replica2
```

4. **Unmount Volumes**

```bash
seto --stack my-stack --driver nfs://user:pass@host unmount-volumes --replica user:pass@replica1 user:pass@replica2
```

5. **Deploy Stack**

```bash
seto --stack my-stack --manager nfs://user@manager-host deploy
```

### Error Handling

The tool includes basic error handling to catch and report errors related to argument parsing and execution. If an error occurs, a message will be printed, and the tool will exit with a non-zero status code.

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
